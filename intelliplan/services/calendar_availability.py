"""A calendar outage is unknown time, never verified free time."""


class CalendarUnavailable(RuntimeError):
    """Safe to render without exposing provider errors or calendar contents."""

    def __init__(self, providers=()):
        self.providers = tuple(p for p in ('google', 'outlook', 'clock') if p in providers)
        super().__init__('A connected calendar could not be checked. Your saved plan is unchanged. '
                         'Try again shortly, or reconnect your calendar in Settings.')

    def response(self):
        return {'status': 'error', 'error_code': 'calendar_unavailable',
                'message': str(self), 'retryable': True, 'acted': False}
