"""StrategyGepgPayment -- the GePG payment strategy.

**Dispatch is a no-op**, for the same reason as StrategyMusePayment: the tasaf_payment
paylist flow owns it.

GePG is reached over GovESB, so there is no REST connector and no transaction log --
initialize_payment_gateway is a no-op and publishing goes through GovESBProducer.

Registered into payroll's PaymentsMethodRegistryPoint by apps.py; selected by
Payroll.payment_method = "StrategyGepgPayment".

_dispatch_chunk and tasks.py are unreachable and are retired in step 6 of that document.
"""

import logging

from django.db import transaction

from payroll.models import BenefitConsumptionStatus
from payroll.strategies.strategy_online_payment import StrategyOnlinePayment

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500

GOVESB_TOPIC_PAYMENT = 'tasaf.payment.instruction.gepg'


class StrategyGepgPayment(StrategyOnlinePayment):
    """
    GePG-specific payment strategy.

    payment_method key (stored on Payroll.payment_method): "StrategyGepgPayment"
    """

    WORKFLOW_NAME = "gepg-payment"
    WORKFLOW_GROUP = "openimis-tasaf-gepg-payment"

    @classmethod
    def make_payment_for_payroll(cls, payroll, user, **kwargs):
        """No-op: dispatch is owned by the tasaf_payment paylist flow.

        This strategy used to send benefit-by-benefit through its own connector, which
        made a second, independent route to the gateway -- one with no batching, no
        50k cap and no two-approver gate. Both routes select `status=ACCEPTED`, and the
        paylist flow never moves a benefit off ACCEPTED, so an already-paid benefit
        stayed a candidate here. That is a double payment waiting on ESB credentials.

        Generation is not wired in yet
        step 3); until then this logs loudly rather than dispatching or failing --
        raising would break payroll's Celery task for no benefit.
        """
        pending = cls.get_benefits_attached_to_payroll(
            payroll, BenefitConsumptionStatus.ACCEPTED,
        ).count()
        logger.warning(
            "%s.make_payment_for_payroll is a no-op: payroll=%s has %d ACCEPTED benefit(s); "
            "disbursement happens in the Tasaf Payments Disbursement tab, which batches, "
            "gates on two approvers and dispatches over GovESB.",
            cls.__name__, payroll.id, pending,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # GovESB is the transport — there is no gateway connector to initialise
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def reconcile_payroll(cls, payroll, user):
        """Close the books from settled paylist items.

        The inherited version asks the gateway what happened. We already know: settlement
        told us, item by item. So reconcile from PROCESSED items only -- returned and
        unapplied benefits stay ACCEPTED and are picked up in a later cycle.

        `reconcile_benefit_consumption` is payroll's own: it stamps a receipt, marks the
        benefit RECONCILED and writes the Bill payment and PaymentInvoice. Accounting
        stays payroll's, with one implementation.
        """
        from payroll.models import BenefitConsumption, PayrollStatus
        from tasaf_payment.models import PaylistItem, PaylistItemStatus

        settled_ids = (
            PaylistItem.objects
            .filter(paylist__payroll_id=payroll.id, is_deleted=False,
                    status=PaylistItemStatus.PROCESSED)
            .values_list('benefit_consumption_id', flat=True)
        )
        benefits = BenefitConsumption.objects.filter(id__in=settled_ids, is_deleted=False)
        count = benefits.count()

        if count:
            cls.reconcile_benefit_consumption(benefits, user)
        cls.change_status_of_payroll(payroll, PayrollStatus.RECONCILED, user)
        logger.info(
            "%s.reconcile_payroll: payroll=%s reconciled %d settled benefit(s)",
            cls.__name__, payroll.id, count,
        )

    @classmethod
    def initialize_payment_gateway(cls, payment_point=None):
        """No-op: GePG is reached over GovESB, not a per-payment-point REST gateway."""
        cls.PAYMENT_GATEWAY = None

    # ──────────────────────────────────────────────────────────────────────────
    # Core dispatch: resolves accounts, publishes to GovESB, updates statuses
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def _dispatch_chunk(cls, payroll_id, benefit_ids: list, user_id: int) -> None:
        """
        Process a single chunk of benefit IDs:
        1. Bulk-load verified PaymentAccounts for all individuals in the chunk.
        2. Publish one GePG instruction per benefit over GovESB.
        3. Batch-update statuses for the instructions that were accepted.
        """
        from payroll.models import BenefitConsumption

        benefits = list(
            BenefitConsumption.objects.filter(
                id__in=benefit_ids,
                status=BenefitConsumptionStatus.ACCEPTED,
                is_deleted=False,
            ).select_related('individual')
        )
        if not benefits:
            return

        account_map = cls._build_account_map(benefits)
        approved_ids, skipped_ids = [], []

        for benefit in benefits:
            account = account_map.get(benefit.individual_id)
            if not account:
                logger.warning(
                    "GepgPayment: no verified primary PaymentAccount for individual %s "
                    "(benefit %s) — skipping",
                    benefit.individual_id, benefit.code,
                )
                skipped_ids.append(benefit.id)
                continue

            payload = {
                'invoice_id':       benefit.code,
                'amount':           str(benefit.amount),
                'account_number':   account.account_number,
                'fsp_code':         account.fsp_name,
                'fsp_type':         account.fsp_type,
                'beneficiary_name': account.account_name,
                'payroll_id':       str(payroll_id),
            }

            result = cls._publish(payload, context=f"benefit={benefit.code}")
            if result.get('published') or result.get('disabled'):
                approved_ids.append(benefit.id)
            else:
                skipped_ids.append(benefit.id)
                logger.info(
                    "GepgPayment: GovESB rejected benefit %s (account %s): %s",
                    benefit.code, account.account_number, result.get('error'),
                )

        if approved_ids:
            with transaction.atomic():
                BenefitConsumption.objects.filter(id__in=approved_ids).update(
                    status=BenefitConsumptionStatus.APPROVE_FOR_PAYMENT
                )
            logger.info(
                "GepgPayment: payroll %s — approved %d, skipped %d of %d benefits",
                payroll_id, len(approved_ids), len(skipped_ids), len(benefits),
            )

    @classmethod
    def _publish(cls, payload: dict, context: str = "") -> dict:
        """
        Publish one instruction over the shared GovESB transport.

        Fail-soft, matching ``tasaf_payment._govesb_publish``: a transport or
        import failure returns an error dict instead of raising, so one bad
        instruction cannot abort the chunk.
        """
        try:
            from coremis_app_integration.govesb import GovESBProducer
        except ImportError as exc:
            logger.warning("GepgPayment: GovESB transport unavailable (%s) — %s", exc, context)
            return {'published': False, 'error': f'transport unavailable: {exc}'}

        try:
            return GovESBProducer().publish(GOVESB_TOPIC_PAYMENT, payload)
        except Exception as exc:  # noqa: BLE001 — fail-soft by design
            logger.exception("GepgPayment: GovESB publish failed — %s", context)
            return {'published': False, 'error': str(exc)}

    @classmethod
    def _build_account_map(cls, benefits: list) -> dict:
        """
        Returns {individual_id: account proxy} for all individuals in *benefits*.

        Single SQL query traversing
        Individual → GroupIndividual → Group → GroupBeneficiary → PaymentAccount.
        """
        from tasaf_payment.models import PaymentAccount, VerificationStatus

        individual_ids = {b.individual_id for b in benefits}

        qs = (
            PaymentAccount.objects
            .filter(
                group_beneficiary__group__groupindividuals__individual_id__in=individual_ids,
                group_beneficiary__group__groupindividuals__is_deleted=False,
                verification_status=VerificationStatus.VERIFIED,
                is_primary=True,
                is_deleted=False,
            )
            .values(
                'id',
                'account_number',
                'account_name',
                'fsp_name',
                'fsp_type',
                'group_beneficiary__group__groupindividuals__individual_id',
            )
            .distinct()
        )

        account_map = {}
        for row in qs:
            ind_id = row['group_beneficiary__group__groupindividuals__individual_id']
            if ind_id not in account_map:
                account_map[ind_id] = _AccountProxy(
                    account_number=row['account_number'],
                    account_name=row['account_name'],
                    fsp_name=row['fsp_name'],
                    fsp_type=row['fsp_type'],
                )
        return account_map


class _AccountProxy:
    """Lightweight stand-in for PaymentAccount used inside _build_account_map."""

    __slots__ = ('account_number', 'account_name', 'fsp_name', 'fsp_type')

    def __init__(self, *, account_number, account_name, fsp_name, fsp_type):
        self.account_number = account_number
        self.account_name = account_name
        self.fsp_name = fsp_name
        self.fsp_type = fsp_type
