"""Production SQL paths must not require the optional MongoDB driver."""
import subprocess
import sys


def test_plain_record_import_and_conversion_with_mongodb_packages_blocked():
    code = '''
import importlib.abc
import sys
class BlockMongo(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'bson', 'pymongo', 'mongomock'}:
            raise AssertionError('SQL record path imported optional MongoDB dependency')
sys.meta_path.insert(0, BlockMongo())
from intelliplan.persistence.manual_tasks import Task
from intelliplan.persistence.schedule_plans import Plan
from intelliplan.persistence.notion import NotionGrant
from intelliplan.persistence.lms_grants import LMSGrant
from intelliplan.persistence.timetable import TimetableRecord
assert Task({'id': 1, 'title': 'Math'}).title == 'Math'
assert Plan({'id': 1, 'schedule_data': '{}'}).schedule_data == '{}'
assert NotionGrant({'id': 1, 'token': 'synthetic'}).token == 'synthetic'
assert LMSGrant('google_classroom', {'id': 1, 'access_token': 'synthetic'}).access_token == 'synthetic'
assert TimetableRecord({'id': 1, 'course': 'Math', 'start_time': '08:00', 'end_time': '09:00'}).to_meeting().course == 'Math'
config = TimetableRecord(configuration=True)
assert config.rotation().kind == 'none' and config.bell() == {}
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
