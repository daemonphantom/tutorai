# Course data

Put crawled courses here, one folder per course: `data/course_<id>/`, where `<id>` is the ISIS course ID. The crawler (`python -m capture.crawling.main`) creates them.

Git ignores everything in this folder except this README. The crawled data contains student names, forum posts and copyrighted teaching material, so keep it out of the repository.

The crawler creates these folders for each course:

```
course_<id>/
    document/      documents.json + files_pdf/, files_docx/, ... (one folder per file type)
    forums/        Teacher announcements and student forum threads, one JSON per forum
    glossaries/    Glossary entries, mostly Q&A (few courses have one)
    image/         image_metadata.json + the image files
    links/         links.json: external links on the course page
    quizzes/       Quiz questions, plus answers when the results page shows them
    resources/     resources.json + files_single/ and files_zip/, mostly homework templates
    users/         Course participants (personal data)
    videos/        videos.json + lecture videos; capture/transcription turns them into text
    webpagetext/   Text of the course main page and its subpages
```

Older crawls may also contain `mainpage/` and `subpages/`; `webpagetext/` replaced both.
