"""Native reads for institution restrictions, feature flags and beta consent."""
from datetime import datetime
from types import SimpleNamespace

from intelliplan.persistence.codec import unpack_document
from intelliplan.persistence.json_values import loads_exact


def record(raw):
    if raw is None:
        return None
    values = unpack_document(raw)
    if isinstance(values.get('policy'), str):
        values['policy'] = loads_exact(values['policy'])
    for field in ('enabled', 'approved'):
        if field in values and type(values[field]) is not bool:
            raise ValueError('Invalid authorization value')
    for field in ('accepted_at',):
        if isinstance(values.get(field), str):
            values[field] = datetime.fromisoformat(values[field])
        if values.get(field) is not None and not isinstance(values[field], datetime):
            raise ValueError('Invalid consent timestamp')
    return SimpleNamespace(**values)


class MongoLearningPolicy:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        for name in ('learning_organizations', 'learning_memberships', 'learning_beta_enrollments', 'feature_flags'):
            self.store.database[name].create_index('id', unique=True)
        self.store.database.learning_memberships.create_index([('user_id', 1), ('organization_id', 1)], unique=True)
        self.store.database.learning_beta_enrollments.create_index([('user_id', 1), ('feature', 1)], unique=True)
        self.store.database.feature_flags.create_index('key', unique=True)

    def organizations(self, user_id):
        def read(db, active):
            if db.users.find_one({'id': int(user_id)}, {'_id': 1}, session=active) is None:
                raise LookupError('Account no longer exists')
            memberships = db.learning_memberships.find({'user_id': int(user_id)}, session=active)
            result = {}
            for membership in memberships:
                identity = membership['organization_id']
                path = set()
                while identity is not None:
                    if identity in path:
                        raise ValueError('Institution hierarchy contains a cycle')
                    if identity in result:
                        break
                    path.add(identity)
                    raw = db.learning_organizations.find_one({'id': identity}, session=active)
                    if raw is None:
                        raise LookupError('Institution policy is unavailable')
                    organization = record(raw)
                    result[identity] = organization
                    identity = getattr(organization, 'parent_id', None)
            return list(result.values())
        return self.store.transaction(read)

    def administers(self, user_id, organization_id):
        org = self.store.database.learning_organizations.find_one({'id': int(organization_id)})
        if org is None:
            return False
        scope = [org['id']]
        if org.get('parent_id') is not None:
            scope.append(org['parent_id'])
        return self.store.database.learning_memberships.find_one({
            'user_id': int(user_id), 'organization_id': {'$in': scope}, 'role': 'admin'}) is not None

    def is_admin(self, user_id):
        return self.store.database.learning_memberships.find_one({'user_id': int(user_id), 'role': 'admin'}) is not None

    def flag(self, key):
        rows = list(self.store.database.feature_flags.find({'key': key}).limit(2))
        if len(rows) > 1:
            raise ValueError('Ambiguous feature flag')
        return record(rows[0]) if rows else None

    def enrollment(self, user_id, feature):
        rows = list(self.store.database.learning_beta_enrollments.find(
            {'user_id': user_id, 'feature': feature}).limit(2))
        if len(rows) > 1:
            raise ValueError('Ambiguous beta enrollment')
        return record(rows[0]) if rows else None
