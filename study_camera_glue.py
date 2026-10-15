"""Wire private device pairing to the existing identity and focus policy."""
from intelliplan.api.study_camera import CameraPairDeps, create_study_camera_blueprint
from active_glue import _current_user_id, _feature_enabled, _focus_allowed


def _allowed():
    from intelliplan.learning import policy
    from flask_login import current_user
    policy.invalidate_cache()
    return (_feature_enabled('active_study') and _focus_allowed()
            and policy.decision(current_user, 'study')[0])


def _create():
    from App import StudyCameraPair, User, db
    return create_study_camera_blueprint(CameraPairDeps(
        db=db, model=StudyCameraPair, user_model=User,
        current_user_id=_current_user_id, allowed=_allowed))


study_camera_bp = _create()


@study_camera_bp.route('/cron/study-cameras', methods=['GET', 'POST'])
def cleanup():
    from flask import jsonify
    from notifications_glue import _cron_authorised
    from intelliplan.services.camera_pairs import cleanup_camera_pairs
    if not _cron_authorised():
        return jsonify(error='Unauthorized maintenance request.'), 401
    return jsonify(removed=cleanup_camera_pairs())
