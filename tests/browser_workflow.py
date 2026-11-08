"""Run against an isolated local SuriQ instance, see TEST_RESULTS.md."""
import os
import re
from pathlib import Path
from time import monotonic

from playwright.sync_api import expect, sync_playwright

from tests.fixtures import BOM, HEADERS, OFFER, TERMS, docx, pdf


def release_request(page, requests):
    deadline = monotonic() + 5
    while not requests and monotonic() < deadline:
        # Give the synchronous Playwright dispatcher time to deliver the route.
        page.wait_for_timeout(20)
    assert requests, "The expected request did not reach the browser router."
    requests[0].continue_()


def main():
    base = os.getenv("SURIQ_TEST_URL", "http://127.0.0.1:3000")
    executable = os.getenv("SURIQ_CHROMIUM")
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        workspace_requests = []
        page.route("**/api/projects", lambda route: workspace_requests.append(route), times=1)
        page.goto(base, wait_until="domcontentloaded")
        expect(page.get_by_role("status").filter(has_text="Loading workspace")).to_be_visible()
        release_request(page, workspace_requests)
        assert page.request.get(f"{base}/api/projects", headers={"Origin": "https://untrusted.example"}).status == 403
        assert page.request.post(f"{base}/api/projects", data={"name": "No intent header"}).status == 403
        page.get_by_role("button", name="New project", exact=True).click()
        page.get_by_label("Project name").fill("Riverside Browser QA")
        page.get_by_role("dialog").get_by_role("button", name="Create project", exact=True).click()
        page.get_by_role("button", name="Create batch", exact=True).click()
        page.get_by_label("Batch name").fill("Steel procurement")
        page.get_by_role("dialog").get_by_role("button", name="Create batch").click()
        expect(page.get_by_role("heading", name="Steel procurement")).to_be_visible()

        third = ["B-014", "Deformed bar 16 mm Grade 60", "60", "pcs", "100", "6000"]
        page.get_by_role("button", name="Upload", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_label("Document type")).to_have_count(0)
        dialog.locator("input[type=file]").set_input_files([
            {"name": filename, "mimeType": "application/pdf", "buffer": content}
            for filename, content in (
                ("quote.pdf", pdf([BOM, ["B-088", "Copper pipe", "2", "pcs", "", ""]], page=2)),
                ("A.pdf", pdf([OFFER], page=3, terms=["Supplier: Supplier A", *TERMS], blank_page=2)),
                ("B.pdf", pdf([OFFER], terms=["Supplier: Supplier B", *TERMS])),
                ("C.pdf", pdf([third], page=4, pages=4, terms=["Supplier: Supplier C"])),
            )
        ])
        expect(dialog.get_by_role("checkbox", name="Allow assisted reading")).to_have_count(0)
        upload_requests = []
        page.route("**/api/batches/*/documents", lambda route: upload_requests.append(route), times=1)
        dialog.get_by_role("button", name="Upload and extract").click()
        expect(dialog.get_by_role("status").filter(has_text="Uploading file 1 of 4")).to_be_visible()
        expect(dialog.get_by_role("progressbar", name="Upload progress")).to_have_attribute("value", "0")
        release_request(page, upload_requests)
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role("button", name="Supplier C")).to_be_visible()

        expect(page.get_by_text("Classification evidence")).to_have_count(0)
        page.get_by_role("button", name="Manage file").click()
        page.get_by_role("button", name="Change document type").click()
        expect(page.get_by_role("dialog").get_by_text("Why this type?")).to_be_visible()
        page.get_by_role("button", name="Close dialog", exact=True).click()
        page.get_by_role("button", name="Supplier C").click()
        page.get_by_label("Supplier coverage").select_option("full")
        expect(page.get_by_label("Supplier coverage")).to_be_enabled()
        page.get_by_role("button", name="Set purchase scope", exact=True).click()
        page.get_by_role("dialog").get_by_label("Scope", exact=True).select_option("all")
        page.get_by_role("button", name="Save scope").click()
        verification_requests = []
        previews = []
        page.route("**/api/batches/*/verify", lambda route: verification_requests.append(route), times=1)
        page.route("**/api/documents/*/page/*", lambda route: previews.append(route), times=1)
        page.get_by_role("button", name="Run verification").click()
        expect(page.get_by_role("button", name="Verifying…")).to_have_attribute("aria-busy", "true")
        release_request(page, verification_requests)
        dimension = page.get_by_role("tab", name="Dimension mismatch")
        expect(dimension).to_have_count(1)
        dimension.click()
        expect(page.get_by_role("tab", name="Quantity mismatch")).to_have_count(1)
        expect(page.get_by_role("tabpanel").locator(".source-card")).to_have_count(3)
        expect(page.get_by_role("status").filter(has_text=re.compile(r"Loading page \d+")).first).to_be_visible()
        release_request(page, previews)
        for file, number in (("quote.pdf", 2), ("A.pdf", 3), ("B.pdf", 1)):
            image = page.get_by_role("img", name=f"Full source Page {number} of {file}")
            image.scroll_into_view_if_needed()
            expect(image).to_be_visible()
            expect(image).to_have_js_property("complete", True)
            assert image.evaluate("img => img.naturalWidth") > 0
        expect(page.locator(".evidence-box")).to_have_count(6)
        if screenshot := os.getenv("SURIQ_TEST_SCREENSHOT"):
            page.screenshot(path=str(Path(screenshot)), full_page=True)
        a = page.locator(".source-card").filter(has=page.get_by_role("img", name="Full source Page 3 of A.pdf"))
        b = page.locator(".source-card").filter(has=page.get_by_role("img", name="Full source Page 1 of B.pdf"))
        marker = a.locator(".evidence-box").first
        relative_left = marker.evaluate("box => box.offsetLeft / box.parentElement.clientWidth")
        a.get_by_role("button", name="Zoom in", exact=True).click()
        expect(a.get_by_role("group", name="Preview zoom")).to_contain_text("125%")
        expect(b.get_by_role("group", name="Preview zoom")).to_contain_text("100%")
        assert a.locator(".paper-scroll").evaluate("pane => pane.scrollWidth > pane.clientWidth")
        assert abs(marker.evaluate("box => box.offsetLeft / box.parentElement.clientWidth") - relative_left) < .01
        viewport = a.get_by_role("region", name="PDF preview of A.pdf")
        viewport.press("+")
        expect(a.get_by_role("group", name="Preview zoom")).to_contain_text("150%")
        viewport.scroll_into_view_if_needed()
        bounds = viewport.bounding_box()
        assert bounds
        before = viewport.evaluate("pane => pane.scrollLeft")
        x = bounds["x"] + bounds["width"] / 2
        y = bounds["y"] + 100
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x - 60, y - 40, steps=5)
        page.mouse.up()
        assert viewport.evaluate("pane => pane.scrollLeft") > before
        a.get_by_role("button", name="Fit to width").click()
        expect(a.get_by_role("group", name="Preview zoom")).to_contain_text("100%")
        page.get_by_role("button", name="Focus highlights").click()
        expect(a.get_by_role("group", name="Preview zoom")).to_contain_text("200%")
        expect(b.get_by_role("group", name="Preview zoom")).to_contain_text("200%")
        assert marker.evaluate("""box => {
          const bounds = box.closest('.paper-scroll').getBoundingClientRect();
          const position = box.getBoundingClientRect();
          return position.left >= bounds.left && position.right <= bounds.right
            && position.top >= bounds.top && position.bottom <= bounds.bottom;
        }""")
        dimension.press("ArrowDown")
        expect(page.get_by_role("tab", name="Quantity mismatch")).to_be_focused()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Documents").click()
        page.get_by_role("button", name="Supplier A").click()
        page.get_by_role("button", name="Original source").click()
        page.get_by_role("button", name="Zoom in", exact=True).click()
        expect(page.get_by_role("group", name="Preview zoom")).to_contain_text("125%")
        page.get_by_role("button", name="Material lines").click()
        page.get_by_role("button", name="Edit Deformed bar 12 mm Grade 60").click()
        page.get_by_label("Dimension", exact=True).fill("16 mm")
        page.get_by_role("dialog").get_by_role("button", name="Zoom in", exact=True).click()
        expect(page.get_by_role("dialog").get_by_role("group", name="Preview zoom")).to_contain_text("125%")
        pan = page.get_by_role("dialog").get_by_role("button", name="Pan preview")
        pan.click()
        expect(pan).to_have_attribute("aria-pressed", "true")
        pan.click()
        expect(page.get_by_role("dialog").locator(".pdf-page")).to_have_class("pdf-page marking")
        corrections = []
        page.route("**/api/documents/*/line/*", lambda route: corrections.append(route), times=1)
        page.get_by_role("button", name="Save correction").click()
        expect(page.get_by_role("button", name="Saving correction…")).to_have_attribute("aria-busy", "true")
        release_request(page, corrections)
        expect(page.get_by_role("dialog")).not_to_be_visible()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Findings").click()
        dimension.click()
        expect(page.get_by_role("tabpanel").locator(".source-card")).to_have_count(2)
        missing = page.get_by_role("tab", name="Item not quoted")
        missing.click()
        page.get_by_role("button", name="Search quotation").click()
        search = page.get_by_role("searchbox", name="Find in document")
        expect(search).to_have_value("B-088")
        search.fill("B-014")
        page.get_by_role("button", name="Search", exact=True).click()
        page.get_by_role("button", name=re.compile("Page 4.*B-014")).click()
        expect(page.get_by_role("heading", name="Search matches")).to_be_visible()
        expect(page.get_by_text("Precise anchor unavailable")).to_have_count(0)
        dimension.click()
        page.get_by_label("Review status").select_option("false_positive")
        page.get_by_label("Internal note").fill("Supplier clarified against the original.")
        page.get_by_role("button", name="Save and next").click()
        expect(dimension).to_have_count(0)
        page.get_by_role("button", name="Comparison", exact=True).click()
        expect(page.get_by_role("heading", name="Offer comparison")).to_be_visible()
        expect(page.get_by_text("Incomparable").first).to_be_visible()
        page.get_by_role("button", name="Line items").click()
        page.get_by_role("checkbox", name="Exceptions only").check()
        expect(page.get_by_role("rowheader", name=re.compile("Copper pipe"))).to_be_visible()
        page.get_by_role("button", name="Overview").click()
        page.get_by_role("button", name="Item not quoted").first.click()
        expect(page.get_by_role("heading", name="Item not quoted")).to_be_visible()
        expect(page.get_by_label("Review filter")).to_have_value("pending")
        page.get_by_role("tab", name="Charges unclear").first.click()
        page.get_by_role("button", name="Link source").click()
        expect(page.get_by_role("dialog").get_by_role("heading", name="Freight")).to_be_visible()
        page.get_by_role("button", name="Close dialog", exact=True).click()
        expect(page.get_by_role("heading", name="Charges unclear")).to_be_visible()
        page.get_by_role("button", name="Find in document").click()
        page.get_by_role("searchbox", name="Find in document").fill("B-014")
        page.get_by_role("button", name="Search", exact=True).click()
        page.get_by_role("button", name=re.compile("Page 4.*B-014")).click()
        page.get_by_role("button", name="Link source on this page").click()
        freight_dialog = page.get_by_role("dialog", name="Edit document terms")
        expect(freight_dialog.get_by_role("combobox", name="Source location in Supplier C")).to_have_value("3")
        page.get_by_role("button", name="Close dialog", exact=True).click()
        page.get_by_role("tab", name="Source needs review").first.click()
        expect(page.get_by_text("Review page 2", exact=True)).to_be_visible()
        thumbnail = page.get_by_role("img", name="Thumbnail of Page 2")
        thumbnail.scroll_into_view_if_needed()
        expect(thumbnail).to_have_js_property("complete", True)
        assert thumbnail.evaluate("img => img.naturalWidth") > 0
        page.get_by_role("button", name="Fit to width").click()
        page.get_by_role("button", name="Find in document").click()
        page.get_by_role("searchbox", name="Find in document").fill("B-014")
        page.get_by_role("button", name="Search", exact=True).click()
        expect(page.get_by_text("Search text is incomplete on some pages or sections.")).to_be_visible()
        page.get_by_text("Export", exact=True).click()
        for extension, heading in (("pdf", "PDF"), ("csv", "CSV")):
            with page.expect_download() as download:
                page.get_by_role("link", name=f"Download {heading}").click()
            assert download.value.suggested_filename.endswith("." + extension)
        page.reload()
        expect(page.get_by_role("heading", name="Steel procurement")).to_be_visible()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Findings").click()
        page.get_by_label("Review filter").select_option("all")
        expect(dimension).to_contain_text("False positive")
        page.set_viewport_size({"width": 390, "height": 844})
        dimension.click()
        mobile_zoom = page.get_by_role("group", name="Preview zoom").first
        for _ in range(12):
            mobile_zoom.get_by_role("button", name="Zoom in", exact=True).click()
        expect(mobile_zoom).to_contain_text("400%")
        expect(mobile_zoom.get_by_role("button", name="Zoom in", exact=True)).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="Activity", exact=True).click()
        expect(page.get_by_text("Material row corrected")).to_be_visible()
        expect(page.locator("main")).not_to_contain_text(re.compile(r"\b(gemini|local|model)\b", re.I))
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="New batch", exact=True).click()
        page.get_by_label("Batch name").fill("Mixed document review")
        page.get_by_role("dialog").get_by_role("button", name="Create batch").click()
        page.get_by_role("button", name="Upload", exact=True).click()
        page.get_by_role("dialog").locator("input[type=file]").set_input_files({
            "name": "combined.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "buffer": docx(["Bill of Materials", "Supplier quotation", "Supplier: Foundry"], [HEADERS, OFFER]),
        })
        page.get_by_role("button", name="Upload and extract").click()
        expect(page.get_by_role("heading", name="Mixed document", exact=True)).to_be_visible()
        page.get_by_role("button", name="Confirm document type").first.click()
        type_dialog = page.get_by_role("dialog", name="Confirm document type")
        type_dialog.get_by_role("button", name=re.compile("Paragraph 1.*Bill of Materials")).click()
        expect(type_dialog.get_by_role("table")).to_be_visible()
        type_dialog.get_by_label("Document type", exact=True).select_option("quote")
        type_dialog.get_by_label("Reason for correction").fill("This file is the supplier’s offer.")
        type_dialog.get_by_role("button", name="Save document type").click()
        expect(page.get_by_role("heading", name="Foundry", exact=True)).to_be_visible()
        page.get_by_role("button", name="Activity", exact=True).click()
        expect(page.get_by_text("Document type corrected", exact=True)).to_be_visible()
        page.get_by_role("button", name="Upload", exact=True).click()
        page.get_by_role("dialog").locator("input[type=file]").set_input_files({
            "name": "unnamed-offer.pdf", "mimeType": "application/pdf",
            "buffer": pdf([OFFER], pages=1, terms=["Quotation", *TERMS]),
        })
        page.get_by_role("button", name="Upload and extract").click()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Documents").click()
        page.get_by_role("button", name=re.compile("Supplier name needed.*unnamed-offer.pdf")).click()
        expect(page.get_by_role("heading", name="Supplier name needed", exact=True)).to_be_visible()
        page.get_by_role("button", name="Add supplier name", exact=True).click()
        expect(page.get_by_role("dialog").get_by_label("Supplier name for quotations")).to_have_value("")
        assert not errors, errors
        browser.close()
    print("Browser workflow passed: intake, grouped evidence, keyboard navigation, correction, review, comparison, exports.")


if __name__ == "__main__":
    main()
