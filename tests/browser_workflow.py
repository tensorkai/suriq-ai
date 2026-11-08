"""Run against an isolated local SuriQ instance, see TEST_RESULTS.md."""
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests.fixtures import BOM, OFFER, TERMS, pdf


def main():
    base = os.getenv("SURIQ_TEST_URL", "http://127.0.0.1:3000")
    executable = os.getenv("SURIQ_CHROMIUM")
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
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
        dialog.get_by_role("button", name="Upload and extract").click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role("button", name="Supplier C")).to_be_visible()

        page.get_by_role("button", name="Set scope").click()
        page.get_by_role("dialog").get_by_label("Scope", exact=True).select_option("all")
        page.get_by_role("button", name="Save scope").click()
        page.get_by_role("button", name="Run verification").click()
        dimension = page.get_by_role("tab", name="Dimension mismatch")
        expect(dimension).to_have_count(1)
        dimension.click()
        expect(page.get_by_role("tab", name="Quantity mismatch")).to_have_count(1)
        expect(page.get_by_role("tabpanel").locator(".source-card")).to_have_count(3)
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
        page.get_by_role("button", name="Save correction").click()
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
        assert not errors, errors
        browser.close()
    print("Browser workflow passed: intake, grouped evidence, keyboard navigation, correction, review, comparison, exports.")


if __name__ == "__main__":
    main()
