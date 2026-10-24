"""Photo practice must be grounded, bounded and safe before it is displayed."""
import io

import pytest
from PIL import Image

from intelliplan.learning import photo_study


def png(size=(320, 240)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'white').save(stream, format='PNG')
    return stream.getvalue()


def test_image_is_decoded_and_metadata_free_before_provider():
    encoded, mime = photo_study.prepare_image(png())
    assert mime == 'image/jpeg'
    import base64
    image = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert image.format == 'JPEG' and not image.getexif()


@pytest.mark.parametrize('payload', [b'', b'not a photo', b'<svg></svg>'])
def test_invalid_images_are_rejected(payload):
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.prepare_image(payload)


def test_oversized_pixels_and_bytes_are_rejected():
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.prepare_image(png((5000, 4100)))
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.prepare_image(b'x' * (photo_study.MAX_IMAGE_BYTES + 1))


def test_generated_card_requires_evidence_from_reviewed_source():
    source = 'Mitosis produces two genetically identical daughter cells.'
    card = {'front': 'What does mitosis produce?', 'back': 'Two identical daughter cells.',
            'source_quote': source}
    result = photo_study.validate_practice({'title': 'Cells', 'items': [card]}, source, 'flashcards', 4)
    assert result['items'][0] == card
    card['source_quote'] = 'Meiosis makes gametes.'
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.validate_practice({'title': 'Cells', 'items': [card]}, source, 'flashcards', 4)


@pytest.mark.parametrize('answer', [True, -1, 4, '0'])
def test_quiz_answer_must_be_an_actual_option_index(answer):
    item = {'question': 'How many cells?', 'options': ['One', 'Two', 'Three', 'Four'],
            'answer': answer, 'explanation': 'The source states two.', 'source_quote': 'two cells'}
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.validate_practice({'title': 'Cells', 'items': [item]}, 'Mitosis makes two cells.', 'quiz', 4)


def test_extraction_preserves_uncertainty_without_inventing_source():
    with pytest.raises(photo_study.InvalidPhoto):
        photo_study.validate_extraction({'title': 'Unreadable', 'text': '', 'warnings': ['Retake the photo.']})
    result = photo_study.validate_extraction({'title': 'Cells', 'text': 'Mitosis makes two cells.',
                                             'warnings': ['Bottom line is unclear.']})
    assert result['warnings'] == ['Bottom line is unclear.']
