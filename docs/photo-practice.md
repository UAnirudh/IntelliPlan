# Practice from a photo

Open **Study tools → Photo practice**, or **Flashcards → From a photo**.

1. Choose a clear photo or use your phone's camera. Notes, a cropped textbook page and labelled diagrams work best. Crop out names and other personal information.
2. Select **Read photo**, then correct the extracted text. Unreadable sections are marked `[unclear]`; the reader is instructed not to guess. You can also paste notes directly.
3. Choose **Interactive quiz** or **Flashcards**, then create practice. Each item includes an expandable quote from your reviewed source. Quiz answers include feedback and explanations; cards keep answers hidden until you reveal them.
4. Save the set to your private flashcard library for spaced review. Quiz questions can also become review cards. A saved set remains available through the ordinary flashcard workflow.

The photo is sent to Groq for reading and is not persisted by this feature. File metadata is removed before the provider request. Only JPEG, PNG and still WebP images up to 6 MB and 20 megapixels are accepted. Generation uses the application's permitted AI providers; school tool restrictions, assessment restrictions, consent, safety checks and account limits still apply.

The supported Groq vision model is `qwen/qwen3.8-27b`, verified against Groq's live model catalog and a synthetic notes image. See [Groq vision documentation](https://console.groq.com/docs/vision). A deployment configured with an unvalidated model fails clearly instead of silently sending photos elsewhere.

Source quotes help inspect a generated answer; they are not a guarantee that AI interpreted the source correctly. Check the source and answers before studying. Very short or unclear material may produce fewer questions or ask for a clearer photo. This is for new practice grounded in notes, rather than generating an answer key to an assessment.
