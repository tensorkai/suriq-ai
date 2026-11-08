"""Run against an isolated local SuriQ instance, see TEST_RESULTS.md."""
import os
import re
from pathlib import Path
from time import monotonic

from playwright.sync_api import expect, sync_playwright

from tests.fixtures import BOM, OFFER, TERMS, pdf


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
                ("quote.pdf", pdf([BOM], page=2)),
                ("A.pdf", pdf([OFFER], page=3, terms=["Supplier: Supplier A", *TERMS])),
                ("B.pdf", pdf([OFFER], terms=["Supplier: Supplier B", *TERMS])),
                ("C.pdf", pdf([third], page=4, pages=4, terms=["Supplier: Supplier C"])),
            )
        ])
        sharing = dialog.get_by_role("checkbox", name=re.compile("Allow assisted reading"))
        if sharing.count():
            expect(sharing).not_to_be_checked()
            expect(dialog.get_by_text("Google may receive excerpts", exact=False)).to_be_visible()
        upload_requests = []
        page.route("**/api/batches/*/documents", lambda route: upload_requests.append(route), times=1)
        dialog.get_by_role("button", name="Upload and extract").click()
        expect(dialog.get_by_role("status").filter(has_text="Uploading file 1 of 4")).to_be_visible()
        expect(dialog.get_by_role("progressbar", name="Upload progress")).to_have_attribute("value", "0")
        release_request(page, upload_requests)
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role("button", name="Supplier C")).to_be_visible()

        page.get_by_role("button", name="Set scope").click()
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
        page.get_by_role("button", name="Focus highlights").click()
        expect(page.get_by_role("button", name="150%", exact=True)).to_be_visible()
        dimension.press("ArrowDown")
        expect(page.get_by_role("tab", name="Quantity mismatch")).to_be_focused()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Documents").click()
        page.get_by_role("button", name="Supplier A").click()
        page.get_by_role("button", name="Edit Deformed bar 12 mm Grade 60").click()
        page.get_by_label("Dimension", exact=True).fill("16 mm")
        corrections = []
        page.route("**/api/documents/*/line/*", lambda route: corrections.append(route), times=1)
        page.get_by_role("button", name="Save correction").click()
        expect(page.get_by_role("button", name="Saving correction…")).to_have_attribute("aria-busy", "true")
        release_request(page, corrections)
        expect(page.get_by_role("dialog")).not_to_be_visible()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Findings").click()
        dimension.click()
        expect(page.get_by_role("tabpanel").locator(".source-card")).to_have_count(2)
        page.get_by_label("Review status").select_option("false_positive")
        page.get_by_label("Internal note").fill("Supplier clarified against the original.")
        page.get_by_role("button", name="Save review").click()
        expect(dimension).to_contain_text("False positive")
        page.get_by_role("button", name="Comparison", exact=True).click()
        expect(page.get_by_role("heading", name="Offer comparison")).to_be_visible()
        expect(page.get_by_text("Incomparable").first).to_be_visible()
        page.get_by_text("Export", exact=True).click()
        for extension, heading in (("pdf", "PDF"), ("csv", "CSV")):
            with page.expect_download() as download:
                page.get_by_role("link", name=f"Download {heading}").click()
            assert download.value.suggested_filename.endswith("." + extension)
        page.reload()
        expect(page.get_by_role("heading", name="Steel procurement")).to_be_visible()
        page.get_by_role("navigation", name="Batch workspace").get_by_role("button", name="Findings").click()
        expect(dimension).to_contain_text("False positive")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="Activity", exact=True).click()
        expect(page.get_by_text("Material row corrected")).to_be_visible()
        expect(page.locator("main")).not_to_contain_text(re.compile(r"\b(gemini|local|model)\b", re.I))
        assert not errors, errors
        browser.close()
    print("Browser workflow passed: intake, grouped evidence, keyboard navigation, correction, review, comparison, exports.")


if __name__ == "__main__":
    main()
