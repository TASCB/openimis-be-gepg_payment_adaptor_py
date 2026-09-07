"""
gepg_payment_adaptor.tasks
===========================
Celery tasks for GePG payment dispatch.

Mirrors ``muse_payment_adaptor.tasks``. Large payrolls fan out so workers
process chunks in parallel and the web request returns immediately.

Task topology for a large payroll
----------------------------------

    dispatch_gepg_payment_chunks
        │
        ├── process_gepg_payment_chunk (chunk 1)  ← worker A
        ├── process_gepg_payment_chunk (chunk 2)  ← worker B
        └── process_gepg_payment_chunk (chunk N)  ← worker C

Each chunk resolves accounts and publishes to GovESB. There is no gateway
to initialise: ``StrategyGepgPayment.initialize_payment_gateway`` is a no-op
because GePG is reached over the shared signed-envelope transport.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500  # benefits per Celery task


def _chunks(iterable, size):
    """Yield successive lists of *size* from *iterable*."""
    items = list(iterable)
    for start in range(0, len(items), size):
        yield items[start:start + size]


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name='gepg_payment_adaptor.dispatch_gepg_payment_chunks',
)
def dispatch_gepg_payment_chunks(self, payroll_id, benefit_ids: list, user_id):
    """
    Fan-out task: splits *benefit_ids* into chunks and dispatches each to
    process_gepg_payment_chunk as an independent Celery task.

    Called by StrategyGepgPayment.make_payment_for_payroll() for large payrolls.
    """
    try:
        chunks = list(_chunks(benefit_ids, _CHUNK_SIZE))
        logger.info(
            "dispatch_gepg_payment_chunks: payroll=%s, %d benefits → %d chunks",
            payroll_id, len(benefit_ids), len(chunks),
        )
        for i, chunk in enumerate(chunks, start=1):
            process_gepg_payment_chunk.delay(payroll_id, chunk, user_id, chunk_index=i)

    except Exception as exc:
        logger.exception("dispatch_gepg_payment_chunks failed for payroll %s", payroll_id)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name='gepg_payment_adaptor.process_gepg_payment_chunk',
)
def process_gepg_payment_chunk(self, payroll_id, benefit_ids: list, user_id, chunk_index=1):
    """Resolve accounts and publish one chunk of benefits to GePG over GovESB."""
    from gepg_payment_adaptor.strategy import StrategyGepgPayment

    try:
        logger.info(
            "process_gepg_payment_chunk: payroll=%s chunk=%s (%d benefits)",
            payroll_id, chunk_index, len(benefit_ids),
        )
        StrategyGepgPayment._dispatch_chunk(payroll_id, benefit_ids, user_id)

    except Exception as exc:
        logger.exception(
            "process_gepg_payment_chunk failed: payroll=%s chunk=%s",
            payroll_id, chunk_index,
        )
        raise self.retry(exc=exc)
