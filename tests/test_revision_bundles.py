from io import BytesIO
import os
import shutil
from subprocess import run

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.fixtures import BOM, scan, xlsx
from tests.test_acceptance import create, ok


def bundle_pdf(*parts):
    output = BytesIO()
    page = Canvas(output)
    for part in parts:
        if part == 'abstract':
            page.drawString(40, 790, 'ABSTRACT OF QUOTATIONS')
            for x, header in zip((40, 210, 320, 435), ('Supplier', 'Reference', 'Scope', 'Materials Total')):
                page.drawString(x, 750, header)
            for y, row in ((728, ('Steel A', 'SQ-A', 'All materials', 'PHP 8,100.00')),
                           (708, ('Steel B', 'SQ-B', 'All materials', 'PHP 8,000.00'))):
                for x, value in zip((40, 210, 320, 435), row):
                    page.drawString(x, y, value)
        elif part == 'continued':
            page.drawString(40, 790, 'Continued from previous page')
            page.drawString(40, 760, 'Payment: 50% on acceptance')
        elif part == 'unclear':
            page.drawString(40, 790, 'Attachment with no visible issuer or page link')
        else:
            supplier, reference = part
            page.drawString(40, 790, supplier)
            page.drawRightString(540, 790, 'QUOTATION')
            if reference:
                page.drawString(40, 760, 'Date: 08 November 2026')
                page.drawRightString(540, 760, f'Document No. {reference}')
            page.drawString(40, 740, 'Scope: All materials')
            page.drawString(40, 720, 'Materials total: PHP 8,000.00')
            page.drawString(40, 700, 'Total contract price: PHP 8,000.00')
        page.showPage()
    page.save()
    return output.getvalue()


def import_pdf(client, batch, parts):
    return ok(client.post(f'/batches/{batch}/documents', files={'file': ('bundle.pdf', bundle_pdf(*parts))}))['id']


def read_bundle(client, batch, parent):
    view = ok(client.get(f'/batches/{batch}'))
    doc = next(item for item in view['documents'] if item['id'] == parent)
    offers = [item for item in view['documents'] if item.get('bundle_parent') == parent and item['bundle_active']]
    return doc, offers, view


@pytest.mark.parametrize('front', [True, False])
def test_split_keeps_abstract_and_original_page_references(tmp_path, monkeypatch, front):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    parts = [('Steel A', 'SQ-A'), 'continued', ('Steel B', 'SQ-B')]
    parts.insert(0 if front else 3, 'abstract')
    first = 2 if front else 1
    abstract = 1 if front else 4
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, parts)
        doc, offers, _ = read_bundle(client, batch, parent)
        assert doc['role'] == 'bundle'
        assert doc['bundle']['reference_pages'] == [abstract]
        assert doc['bundle']['pending_pages'] == []
        assert [(item['supplier'], item['bundle_pages']) for item in offers] == [
            ('Steel A', [first, first + 1]), ('Steel B', [first + 2]),
        ]
        assert offers[0]['terms']['payment'] == '50% on acceptance'
        assert offers[1]['terms']['materials_total'] == 'PHP 8,000.00'
        assert client.get(f'/documents/{offers[0]["id"]}/page/{first + 1}').content.startswith(b'\x89PNG')
        assert client.get(f'/documents/{offers[0]["id"]}/page/{abstract}').status_code == 404
        assert client.get(f'/documents/{parent}/original').content.startswith(b'%PDF')

        ok(client.post(f'/batches/{batch}/documents', data={'role': 'bom'}, files={'file': ('bom.xlsx', xlsx([BOM]))}))
        ok(client.patch(f'/batches/{batch}/scope', json={'mode': 'all'}))
        verified = ok(client.post(f'/batches/{batch}/verify'))
        assert len(verified['comparison']) == 2
        assert [(link['supplier'], link['matches']) for link in verified['reference_links']] == [
            ('Steel A', False), ('Steel B', True),
        ]
        issue, = [item for item in verified['issues'] if item['rule'] == 'reference_discrepancy']
        assert [(item['document'], item['source']) for item in issue['evidence']] == [(parent, abstract), (offers[0]['id'], first)]
        assert '8,100.00' in issue['note'] and '8,000.00' in issue['note']


def test_bundle_correction_rollback_retry_and_undo(tmp_path, monkeypatch):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    parts = [('Steel A', 'SQ-A'), 'unclear', ('Steel A', 'SQ-B'), 'abstract']
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, parts)
        doc, offers, view = read_bundle(client, batch, parent)
        assert doc['bundle']['pending_pages'] == [2] and offers == []
        assert 'bundle' in view['verification_problem'].lower()
        route = f'/documents/{parent}/bundle'
        change = {'revision': doc['bundle']['revision'], 'offers': doc['bundle']['offers'],
                  'reference_pages': [4], 'excluded': [{'page': 2, 'reason': 'Unrelated attachment'}]}
        invalid = {**change, 'reference_pages': [1, 4]}
        assert client.patch(route, json=invalid).status_code == 422
        assert read_bundle(client, batch, parent)[0]['bundle'] == doc['bundle']
        ok(client.patch(route, json=change))
        updated, offers, _ = read_bundle(client, batch, parent)
        assert [offer['terms']['reference'] for offer in offers] == ['SQ-A', 'SQ-B']
        ids = [offer['id'] for offer in offers]
        ok(client.post(f'/documents/{parent}/retry'))
        assert [offer['id'] for offer in read_bundle(client, batch, parent)[1]] == ids
        ok(client.patch(f'/documents/{ids[0]}/terms', json={'values': {'payment': 'Confirm before dispatch'}}))
        undo = {'revision': updated['bundle']['revision'], 'undo': True}
        assert client.patch(route, json=undo).status_code == 422
        assert [offer['id'] for offer in read_bundle(client, batch, parent)[1]] == ids
        ok(client.patch(route, json={**undo, 'confirm_archive': True}))
        undone, offers, view = read_bundle(client, batch, parent)
        assert undone['bundle']['pending_pages'] == [2] and offers == []
        assert all(any(item['id'] == key and not item['bundle_active'] for item in view['documents']) for key in ids)
        assert next(item for item in view['documents'] if item['id'] == ids[0])['terms']['payment'] == 'Confirm before dispatch'


def test_abstract_only_and_over_cap_bundle_remain_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, ['abstract'])
        doc, offers, _ = read_bundle(client, batch, parent)
        assert doc['bundle']['reference_pages'] == [1] and offers == []
        assert not doc['bundle']['pending_pages'] and not doc['terms'] and not doc['lines']
        parent = ok(client.post(f'/batches/{batch}/documents', files={
            'file': ('six-offers.pdf', bundle_pdf(*[(f'Steel {i}', f'SQ-{i}') for i in range(6)])),
        }))['id']
        doc, offers, _ = read_bundle(client, batch, parent)
        assert offers == [] and len(doc['bundle']['offers']) == 6
        assert doc['bundle']['reason']
        ok(client.patch(f'/documents/{parent}/bundle', json={
            'revision': doc['bundle']['revision'], 'offers': doc['bundle']['offers'][:5],
            'excluded': [{'page': 6, 'reason': 'Offer outside purchase shortlist'}],
        }))
        assert len(read_bundle(client, batch, parent)[1]) == 5


def test_same_supplier_quote_references_are_distinct_and_completed_canvass_is_offer(tmp_path, monkeypatch):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, [('Steel A', 'SQ-A'), ('Steel A', 'SQ-B')])
        doc, offers, _ = read_bundle(client, batch, parent)
        assert not doc['bundle']['pending_pages']
        assert [(offer['terms']['reference'], offer['bundle_pages']) for offer in offers] == [('SQ-A', [1]), ('SQ-B', [2])]

        output = BytesIO()
        page = Canvas(output)
        page.drawString(40, 790, 'ABSTRACT OF CANVASS')
        page.drawString(40, 770, 'Quotation')
        page.drawString(40, 750, 'Supplier: Metal Company')
        page.drawString(40, 730, 'Quotation No. CO-118')
        page.drawString(40, 710, 'Materials total: PHP 8,000.00')
        page.showPage()
        page.save()
        key = ok(client.post(f'/batches/{batch}/documents', files={'file': ('canvass.pdf', output.getvalue())}))['id']
        doc, _, _ = read_bundle(client, batch, key)
        assert doc['role'] == 'quote' and doc['summary_only']
        assert doc['terms']['reference'] == 'CO-118'


def test_repeated_letterhead_without_references_remains_pending(tmp_path, monkeypatch):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, [('Steel A', ''), ('Steel A', '')])
        doc, offers, _ = read_bundle(client, batch, parent)
        assert doc['bundle']['pending_pages'] == [1, 2]
        assert offers == []


def test_unrelated_layout_change_preserves_corrected_supplier(tmp_path, monkeypatch):
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, [('Steel A', 'SQ-A'), ('Steel B', 'SQ-B'), 'abstract'])
        doc, offers, _ = read_bundle(client, batch, parent)
        first = offers[0]['id']
        ok(client.patch(f'/documents/{first}/role', data={
            'role': 'quote', 'supplier': 'Steel A Incorporated', 'reason': 'Confirmed the legal name.',
        }))
        ok(client.patch(f'/documents/{parent}/bundle', json={
            'revision': doc['bundle']['revision'], 'offers': doc['bundle']['offers'],
            'excluded': [{'page': 3, 'reason': 'Superseded reference sheet'}],
        }))
        updated = next(offer for offer in read_bundle(client, batch, parent)[1] if offer['id'] == first)
        assert updated['supplier'] == 'Steel A Incorporated'


def test_bundle_pdf_failure_rolls_back_all_records(tmp_path, monkeypatch):
    from service import bundles
    from service.extraction import ExtractionError

    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    parts = [('Steel A', 'SQ-A'), 'continued', ('Steel B', 'SQ-B'), 'abstract']
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = import_pdf(client, batch, parts)
        doc, offers, view = read_bundle(client, batch, parent)
        original_extract = bundles.extract_pages

        def fail_second(original, pages):
            if pages == (2, 3):
                raise ExtractionError('Synthetic split failure')
            return original_extract(original, pages)

        monkeypatch.setattr(bundles, 'extract_pages', fail_second)
        response = client.patch(f'/documents/{parent}/bundle', json={
            'revision': doc['bundle']['revision'], 'reference_pages': [4], 'offers': [
                {'supplier': 'Steel A', 'reference': 'SQ-A', 'pages': [1]},
                {'supplier': 'Steel B', 'reference': 'SQ-B', 'pages': [2, 3]},
            ],
        })
        assert response.status_code == 422
        current, active, after = read_bundle(client, batch, parent)
        assert current['bundle'] == doc['bundle']
        assert [item['id'] for item in active] == [item['id'] for item in offers]
        assert {item['id'] for item in after['documents']} == {item['id'] for item in view['documents']}


def test_scan_heavy_bundle_leaves_unreadable_page_pending(tmp_path, monkeypatch):
    if not shutil.which(os.getenv('SURIQ_TESSERACT', 'tesseract')):
        pytest.skip('Tesseract is required for scanned bundle acceptance.')
    monkeypatch.setenv('SURIQ_AI_PROVIDER', 'off')
    parts = [
        scan(bundle_pdf(('Steel A', 'SQ-A'))), scan(),
        bundle_pdf(('Steel B', 'SQ-B'), 'abstract'),
    ]
    paths = []
    for i, part in enumerate(parts):
        path = tmp_path / f'part-{i}.pdf'
        path.write_bytes(part)
        paths.append(str(path))
    original = tmp_path / 'scanned-bundle.pdf'
    run(['pdfunite', *paths, str(original)], check=True)
    with TestClient(create_app(tmp_path / 'bundle.sqlite')) as client:
        batch = create(client)
        parent = ok(client.post(f'/batches/{batch}/documents', files={
            'file': ('scan.pdf', original.read_bytes()),
        }))['id']
        doc, offers, view = read_bundle(client, batch, parent)
        assert doc['bundle']['reference_pages'] == [4]
        assert 2 in doc['bundle']['pending_pages'] and offers == []
        assert doc['sources'][1]['method'] == 'ocr' and doc['sources'][1]['warning']
        assert view['verification_action'] == {'kind': 'read', 'document': parent}
        assert client.get(f'/documents/{parent}/page/2').content.startswith(b'\x89PNG')
