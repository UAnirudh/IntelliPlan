"""Photo -> reviewed source -> safe practice -> ordinary spaced-review deck."""
from __future__ import annotations

import functools
import hashlib
import json
import logging
from datetime import datetime, timezone

from flask import current_app, jsonify, redirect, render_template, request
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select

import ai_firewall
import ai_provider
import plani_safety
from intelliplan.learning import photo_study as study
from intelliplan.learning.enforcement import permitted_processing
from .api import flashcards_bp, _db
from . import store

logger = logging.getLogger(__name__)
RECEIPT_LIFETIME = 45 * 60


class UnsafePhoto(Exception):
    pass


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='photo-practice-v1')


def _screen(text):
    if not isinstance(text, str) or not text.strip() or len(text) > study.MAX_RESULT_CHARS:
        raise study.InvalidPhoto('The result was too long or incomplete. Try a smaller section.')
    from intelliplan.learning.safety_terms import allowed_terms_for_student
    terms = allowed_terms_for_student(current_user.id)
    if plani_safety.classify([{'role': 'user', 'content': text}], allowed_terms=terms):
        raise UnsafePhoto()


def _access():
    # The central surface gate enforces role, assessment and school tool policy.
    # These additional checks cover consent and the specific disclosed provider.
    from chatbot_api import _schoolwork_access_error
    error = _schoolwork_access_error()
    if error is not None:
        return error
    if not permitted_processing('groq'):
        return jsonify(error='provider_restricted', message='Your school has not enabled Groq photo and safety processing.'), 403
    return None


def _api(handler):
    @functools.wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            access = _access()
            response = access if access is not None else handler(*args, **kwargs)
        except study.InvalidPhoto as exc:
            response = jsonify(error='invalid_photo_practice', message=str(exc)), 422
        except UnsafePhoto:
            response = jsonify(error='safety_refusal', message='This material cannot be used for school practice. Choose age-appropriate study notes.'), 403
        except plani_safety.SafetyUnavailable:
            response = jsonify(error='safety_unavailable', message='The safety check is unavailable. Nothing was saved; try again shortly.'), 503
        except ai_firewall.AIBlocked as exc:
            response = jsonify(error=exc.reason, message=exc.message), exc.status
            if exc.retry_after:
                response = (*response, {'Retry-After': str(exc.retry_after)})
        except ai_provider.AIAllowanceExceeded:
            response = jsonify(error='ai_allowance', message='Your account has reached its AI allowance. Try again after it resets.'), 429
        except ai_provider.AIQuotaExhausted as exc:
            response = jsonify(error='provider_busy', message='The photo provider is busy. Try again shortly.'), 429, {'Retry-After': str(exc.retry_after or 60)}
        except ai_provider.AIUnavailable:
            response = jsonify(error='provider_unavailable', message='The photo reader could not finish. Try a smaller, clearer section shortly.'), 503
        except Exception as exc:
            _db().session.rollback()
            # No image, prompt, source text, provider response or credentials.
            logger.warning('Photo practice failed (%s)', type(exc).__name__)
            response = jsonify(error='photo_practice_unavailable', message='Practice could not be completed. Nothing was saved; please try again.'), 503
        result = current_app.make_response(response)
        result.headers['Cache-Control'] = 'no-store, private'
        return result
    return wrapped


@flashcards_bp.get('/flashcards/photo')
def photo_page():
    if not current_user.is_authenticated:
        return redirect('/login/account?next=/flashcards/photo')
    return render_template('photo_study.html', active_page='flashcards', noindex_page=True)


@flashcards_bp.post('/api/flashcards/photo/extract')
@_api
def photo_extract():
    if request.content_length and request.content_length > study.MAX_IMAGE_BYTES + 128 * 1024:
        raise study.InvalidPhoto('Choose a photo smaller than 6 MB.')
    upload = request.files.get('image')
    if upload is None:
        raise study.InvalidPhoto('Choose a photo of your notes first.')
    encoded, mime = study.prepare_image(upload.read(study.MAX_IMAGE_BYTES + 1))
    decision = ai_firewall.guard(current_user, prompts=['Read study notes from one photo.'],
                                want_output_tokens=3000, feature='photo_reading')
    raw = ai_provider.vision(system_prompt=study.EXTRACTION_PROMPT,
        user_text='Transcribe this photo faithfully for review. Mark uncertain parts; do not guess.',
        image_b64=encoded, image_mime=mime, temperature=0, provider='groq',
        max_tokens=decision.max_output_tokens)
    ai_firewall.record_tokens(decision, len(study.EXTRACTION_PROMPT), len(raw))
    _screen(raw)  # Include every field and suffix before returning any OCR text.
    result = study.validate_extraction(study.parse_result(raw))
    return jsonify(**result, provider='groq', image_stored=False)


@flashcards_bp.post('/api/flashcards/photo/generate')
@_api
def photo_generate():
    if request.content_length and request.content_length > 60_000:
        raise study.InvalidPhoto('Source text is too long. Keep one topic under 12,000 characters.')
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise study.InvalidPhoto('Provide reviewed source text and choose a practice mode.')
    source = study.reviewed_source(body.get('source'))
    mode, count = body.get('mode'), body.get('count', 6)
    if mode not in {'quiz', 'flashcards'} or type(count) is not int or not 1 <= count <= 10:
        raise study.InvalidPhoto('Choose quiz or flashcards and between 1 and 10 items.')
    decision = ai_firewall.guard(current_user, prompts=[source], want_output_tokens=4000,
                                feature='photo_practice')
    _screen(source)  # Edits and pasted text receive the same full safety check.
    prompt = json.dumps({'mode': mode, 'maximum_items': count, 'reviewed_source': source}, ensure_ascii=False)
    result = ai_provider.chat_json([
        {'role': 'system', 'content': study.PRACTICE_PROMPT},
        {'role': 'user', 'content': prompt}], temperature=0.25,
        max_tokens=decision.max_output_tokens)
    raw = json.dumps(result, ensure_ascii=False)
    ai_firewall.record_tokens(decision, len(prompt) + len(study.PRACTICE_PROMPT), len(raw))
    _screen(raw)
    practice = study.validate_practice(result, source, mode, count)
    receipt = _serializer().dumps({'version': 1, 'user_id': current_user.id,
                                 'source': source, 'practice': practice})
    return jsonify(**practice, receipt=receipt)


@flashcards_bp.post('/api/flashcards/photo/save')
@_api
def photo_save():
    if request.content_length and request.content_length > 100_000:
        raise study.InvalidPhoto('This practice result is too large. Create it again from a smaller section.')
    body = request.get_json(silent=True)
    token = body.get('receipt') if isinstance(body, dict) else None
    if not isinstance(token, str) or len(token) > 80_000:
        raise study.InvalidPhoto('Create a practice set before saving it.')
    try:
        data = _serializer().loads(token, max_age=RECEIPT_LIFETIME)
    except BadData as exc:
        raise study.InvalidPhoto('This practice preview expired. Create it again before saving.') from exc
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('user_id') != current_user.id:
        return jsonify(error='practice_not_found', message='Create a practice set in this account before saving.'), 404
    source = study.reviewed_source(data.get('source'))
    practice_data = data.get('practice')
    if not isinstance(practice_data, dict):
        raise study.InvalidPhoto('Create a practice set before saving it.')
    practice = study.validate_practice(practice_data, source, practice_data.get('mode'), 10)
    # School permissions, consent and the current safety policy are checked
    # again, including when an old browser preview is used after a policy change.
    ai_firewall.guard(current_user, prompts=[source], want_output_tokens=1, feature='photo_save')
    _screen(source)
    _screen(json.dumps(practice, ensure_ascii=False))
    cards = study.cards_from_practice(practice)
    database = _db()
    store.ensure_tables(database)
    identifier = 'photo:' + hashlib.sha256(token.encode()).hexdigest()[:18]
    from App import User
    # Serialize saves for this account before the idempotency lookup. A check
    # without a row lock permits simultaneous tabs to create duplicate decks.
    database.session.execute(select(User.id).where(User.id == current_user.id).with_for_update()).scalar_one()
    existing = database.session.execute(select(store.DECKS.c.id).where(
        store.DECKS.c.user_id == current_user.id, store.DECKS.c.source == identifier)).scalar()
    if existing:
        return jsonify(id=existing, added=len(cards), url='/flashcards', already_saved=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # One transaction: no empty saved deck if the card insert fails.
    inserted = database.session.execute(store.DECKS.insert().values(
        user_id=current_user.id, name=practice['title'], source=identifier,
        description='Practice created from your reviewed photo notes.', archived=False,
        created_at=now, updated_at=now))
    deck_id = int(inserted.inserted_primary_key[0])
    database.session.execute(store.CARDS.insert(), [dict(card, user_id=current_user.id,
        deck_id=deck_id, tags='photo-practice', card_type='basic', state='new',
        suspended=False, due=now, created_at=now) for card in cards])
    database.session.commit()
    return jsonify(id=deck_id, added=len(cards), url='/flashcards'), 201
