"""Explicit native account-erasure policy; shared content is anonymized.

This is an ownership plan, not a SQL translation layer. Keep the policy in
sync with account-deletion registry coverage whenever a domain adds an owner.
"""
from decimal import Decimal
import json

from intelliplan.persistence.codec import unpack_document, update_fields
from intelliplan.learning.governance import anonymize_audit_value


DIRECT_USER_COLLECTIONS = (
    'learning_beta_enrollments',
    'learning_beta_feedback',
    'learning_monthly_budgets',
    'learning_evidence',
    'learning_courses',
    'learning_sittings',
    'learning_memberships',
    'google_integrations',
    'outlook_integrations',
    'google_drive_integrations',
    'onedrive_integrations',
    'cloud_document_links',
    'notion_integrations',
    'canvas_integrations',
    'classroom_integrations',
    'blackboard_integrations',
    'moodle_integrations',
    'lms_tokens',
    'linked_accounts',
    'resource_accounts',
    'user_identities',
    'api_keys',
    'extension_tokens',
    'course_checkins',
    'tracked_courses',
    'desktop_auth_codes',
    'app_link_codes',
    'focus_shield_settings',
    'accessibility_prefs',
    'student_profiles',
    'adaptive_student_profile',
    'tutor_conversations',
    'tutor_memory',
    'policy_acknowledgements',
    'security_events',
    'password_reset_tokens',
    'manual_tasks',
    'manual_courses',
    'class_meetings',
    'timetable_settings',
    'saved_schedules',
    'scheduler_presets',
    'manual_plan_presets',
    'schedule_decisions',
    'schedule_versions',
    'day_archives',
    'task_feedback',
    'dismissed_assignments',
    'test_marks',
    'custom_descriptions',
    'imported_grades',
    'memory_chunks',
    'course_notes',
    'lessons',
    'study_sessions',
    'active_sessions',
    'study_camera_pairs',
    'assignment_steps',
    'study_points',
    'study_mastery',
    'concept_mastery',
    'learning_events',
    'user_streaks',
    'plani_pets',
    'session_messages',
    'syllabus_records',
    'saved_meetings',
    'media_balance_sessions',
    'media_balance_prefs',
    'briefing_cache',
    'health_snapshots',
    'student_signals',
    'push_subscriptions',
    'reminders_sent',
    'notification_outbox',
    'grade_pulse_snapshots',
    'calendar_feed_tokens',
    'email_sends',
    'feature_request_votes',
    'feature_requests',
    'site_feedback',
    'ai_usage',
    'ai_spend_events',
    'ai_credit_grants',
    'linked_ai_accounts',
    'product_events',
    'insight_answers',
    'client_error_logs',
    'voice_seats',
    'study_group_members',
    'fc_reviews', 'fc_cards', 'fc_decks',
)

DIRECT_OTHER_OWNERS = {
    'tutor_check_hint': ('owner_id',), 'tutor_scored_check': ('owner_id',),
    'tutor_education_plan': ('owner_id',), 'primer_learner': ('owner_id',),
    'live_sessions': ('owner_id',), 'buddy_nudges': ('sender_id', 'recipient_id'),
    'study_buddies': ('user_low_id', 'user_high_id', 'requested_by_id', 'blocked_by_id'),
    'student_links': ('linker_user_id', 'student_user_id'),
}
ANONYMIZED_REFERENCES = {
    'learning_policy_audit': {'actor_id': None},
    'school_outreach_leads': {'user_id': None, 'student_email': ''},
    'manual_entry_reasons': {'user_id': None},
    'study_group_tasks': {'claimed_by_user_id': None},
    'study_groups': {'owner_id': None},
    'users': {'referred_by_id': None},
}


def _exact_json_text(value):
    """Serialize parsed JSON numbers without converting Decimal to float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Audit JSON contains a non-finite number')
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(_exact_json_text(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + _exact_json_text(item)
                              for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _audit_changes(value, user_id):
    if not isinstance(value, str):
        return anonymize_audit_value(value, user_id)

    def invalid_number(raw):
        raise ValueError('Audit JSON contains a non-JSON number')

    # Ordinary integer references remain ints for the shared scrubber. Very
    # large unrelated integers avoid Python's bounded int-string conversion.
    decoded = json.loads(value, parse_float=Decimal, parse_constant=invalid_number,
                         parse_int=lambda raw: int(raw) if len(raw) <= 20 else Decimal(raw))
    cleaned = anonymize_audit_value(decoded, user_id)
    return value if cleaned == decoded else _exact_json_text(cleaned)


class MongoErasure:
    def __init__(self, store, *, session_decoder=None):
        self.store = store
        self.session_decoder = session_decoder

    @staticmethod
    def _upload_names(db, user_id, active=None):
        result = {}
        for name in ('course_notes', 'lessons'):
            values = [row.get('stored_filename') for row in db[name].find(
                {'user_id': int(user_id)}, {'stored_filename': 1}, session=active)]
            if any(value is not None and not isinstance(value, str) for value in values):
                raise ValueError('Invalid stored upload name')
            result[name] = sorted(value for value in values if value)
            if name == 'lessons' and result[name] and db.lessons.find_one({
                'user_id': {'$ne': int(user_id)}, 'stored_filename': {'$in': result[name]},
            }, {'_id': 1}, session=active) is not None:
                raise RuntimeError('A stored upload is referenced by another account')
        return result

    def upload_names(self, user_id):
        return self._upload_names(self.store.database, user_id)

    def erase(self, user_id, *, expected_uploads=None, pending_token=None):
        user_id = int(user_id)
        if pending_token is not None and (not isinstance(pending_token, str) or not pending_token):
            raise ValueError('A pending approval token is required')

        def write(db, active):
            owner = {'id': user_id}
            if pending_token is not None:
                owner.update(parent_consent_token=pending_token, parent_consent_granted={'$ne': True})
            if not db.users.update_one(owner, {'$inc': {'_erasure_revision': 1}},
                                       session=active).matched_count:
                return False
            # File staging happens before this transaction. Abort if any
            # writer changed the referenced files between staging and locking.
            if expected_uploads is not None and self._upload_names(db, user_id, active) != expected_uploads:
                raise RuntimeError('Account uploads changed during deletion; retry')

            def ids(name, query):
                return [row['id'] for row in db[name].find(query, {'id': 1}, session=active)]

            profiles = ids('adaptive_student_profile', {'user_id': user_id})
            learners = ids('primer_learner', {'owner_id': user_id})
            courses = ids('learning_courses', {'user_id': user_id})
            sittings = ids('active_sessions', {'user_id': user_id})
            requests = ids('feature_requests', {'user_id': user_id})
            links = ids('student_links', {'$or': [
                {'linker_user_id': user_id}, {'student_user_id': user_id}]})
            child_scopes = {
                'learning_generation_jobs': ('course_id', courses),
                'learning_lessons': ('course_id', courses),
                'active_focus_samples': ('session_id', sittings),
                'feature_request_votes': ('request_id', requests),
                'family_nudges': ('link_id', links),
            }
            for name in ('adaptive_session_summary', 'adaptive_memory_import', 'adaptive_learner_memory',
                         'adaptive_mistake_pattern', 'adaptive_subject_mastery'):
                child_scopes[name] = ('profile_id', profiles)
            for name in ('primer_hint_used', 'primer_attempt', 'primer_skill_state', 'primer_journey',
                         'primer_nudge', 'primer_offline_checkin', 'primer_parent_setting',
                         'primer_learning_profile'):
                child_scopes[name] = ('learner_id', learners)
            for name, (field, values) in child_scopes.items():
                if values:
                    db[name].delete_many({field: {'$in': values}}, session=active)

            for name in DIRECT_USER_COLLECTIONS:
                db[name].delete_many({'user_id': user_id}, session=active)
            for name, fields in DIRECT_OTHER_OWNERS.items():
                db[name].delete_many({'$or': [{field: user_id} for field in fields]}, session=active)
            db.study_group_tasks.delete_many({'created_by_user_id': user_id}, session=active)

            for name, values in ANONYMIZED_REFERENCES.items():
                field = next(iter(values))
                db[name].update_many({field: user_id}, update_fields(values), session=active)
            for row in db.learning_policy_audit.find({}, session=active):
                changes = unpack_document(row).get('changes')
                cleaned = _audit_changes(changes, user_id)
                if cleaned != changes:
                    db.learning_policy_audit.update_one({'_id': row['_id']},
                        update_fields({'changes': cleaned}), session=active)

            # New sessions carry an indexed owner. Legacy migrated binary
            # sessions are decoded with the configured session serializer.
            db.sessions.delete_many({'user_id': user_id}, session=active)
            for row in db.sessions.find({'user_id': {'$exists': False}}, session=active):
                if self.session_decoder is None:
                    raise RuntimeError('A legacy session decoder is required for erasure')
                values = self.session_decoder(bytes(row['data']))
                if str(values.get('_user_id', '')) == str(user_id):
                    db.sessions.delete_one({'_id': row['_id']}, session=active)
            db.users.delete_one({'id': user_id}, session=active)
            return True

        return self.store.transaction(write)
