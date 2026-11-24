from pathlib import Path
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrMacOptions


def convert_pdf_to_markdown(pdf_path: str, output_md: str):
    """Convert PDF to markdown without OCRing logos."""
    pdf_opts = PdfPipelineOptions()
    pdf_opts.do_ocr = True
    pdf_opts.force_backend_text = True
    pdf_opts.do_picture_description = False
    pdf_opts.picture_description_options.picture_area_threshold = 0.20
    pdf_opts.ocr_options = OcrMacOptions(
        force_full_page_ocr=False,    # hybrid: only OCR where needed
        bitmap_area_threshold=0.15    # ignore <15% of page area
    )
    
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)}
    )
    
    doc = converter.convert(pdf_path).document
    N = len(doc.pages)
    
    pages = [doc.export_to_markdown(page_no=i) or "" for i in range(1, N + 1)]
    if not pages[0].strip():
        pages = [doc.export_to_markdown(page_no=i) or "" for i in range(N)]
    
    chunks = [f"<!-- PAGE {i+1}/{N} -->\n{p}" for i, p in enumerate(pages)]
    Path(output_md).write_text("\n\n".join(chunks), encoding="utf-8")


def main():
    import sys
    if len(sys.argv) > 2:
        pdf_path = sys.argv[1]
        output_md = sys.argv[2]
    else:
        pdf_path = "data/course_43321/document/files_pdf/43321_002_01_document.pdf"
        output_md = "output_43321_002_01.md"
    
    convert_pdf_to_markdown(pdf_path, output_md)
    print(f"Markdown written to {output_md}")


if __name__ == "__main__":
    main()
