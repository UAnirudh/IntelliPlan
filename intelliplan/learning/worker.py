"""Explicit course-worker operation using the existing durable job leases."""
from contextlib import contextmanager
import json
import math
import os
import signal
import sys
from threading import Event

import click

from intelliplan.learning import courses


def run_batch(steps, stop):
    from App import db
    processed = 0
    for _ in range(steps):
        if stop.is_set():
            break
        try:
            available = courses.run_one_job()
        finally:
            # Return idle connections and ORM state between durable steps.
            db.session.remove()
        if not available:
            break
        processed += 1
    return processed


@contextmanager
def shutdown_signals(stop):
    """Finish the current leased step before exiting on SIGINT or SIGTERM."""
    previous = []
    def request_stop(signum, frame):
        stop.set()
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handler = signal.getsignal(signum)
            try:
                signal.signal(signum, request_stop)
            except ValueError:
                raise click.ClickException('Watch mode must run in the main process thread.') from None
            previous.append((signum, handler))
        yield
    finally:
        for signum, handler in reversed(previous):
            signal.signal(signum, handler)


def event(name, **values):
    # Counts and fixed event names only; never log prompts or student records.
    click.echo(json.dumps(dict(event=name, **values)))


def install(app):
    @app.cli.command('learning-worker')
    @click.option('--steps', default=1, type=click.IntRange(1, 1000),
                  help='Maximum durable steps per batch.')
    @click.option('--watch', is_flag=True,
                  help='Keep processing new jobs until SIGINT or SIGTERM. Requires an external supervisor.')
    @click.option('--poll-seconds', default=5.0, type=click.FloatRange(0.1, 60.0),
                  help='Idle polling interval in watch mode.')
    def worker(steps, watch, poll_seconds):
        """Process course generation. No worker is started by web requests."""
        if not math.isfinite(poll_seconds):
            raise click.BadParameter('Use a finite idle polling interval.', param_hint='--poll-seconds')
        stop = Event()
        total = 0
        try:
            if not watch:
                total = run_batch(steps, stop)
                click.echo(f'Processed {total} generation steps.')
                return
            with shutdown_signals(stop):
                event('worker_started', batch_steps=steps, poll_seconds=poll_seconds)
                try:
                    while not stop.is_set():
                        processed = run_batch(steps, stop)
                        total += processed
                        if processed:
                            event('batch_processed', steps=processed, total_steps=total)
                        else:
                            stop.wait(poll_seconds)
                except KeyboardInterrupt:
                    stop.set()
                finally:
                    event('worker_stopped', total_steps=total)
        except Exception as exc:
            event('worker_failed', error_type=type(exc).__name__)
            if isinstance(exc, click.ClickException):
                raise
            raise click.ClickException('Course worker failed. Inspect service health before restarting.') from None


def main():
    """Disable unrelated timers before the dedicated process imports App."""
    os.environ['FOLLOWTHROUGH_INPROCESS_CRON'] = '0'
    os.environ['NOTIFICATIONS_INPROCESS_CRON'] = '0'
    from flask.cli import cli as flask_cli
    flask_cli.main(args=['--app', 'App', 'learning-worker', *sys.argv[1:]])


if __name__ == '__main__':
    main()
