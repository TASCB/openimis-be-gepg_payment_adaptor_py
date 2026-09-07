from django.apps import AppConfig


class GepgPaymentAdaptorConfig(AppConfig):
    name = 'gepg_payment_adaptor'

    def ready(self):
        """
        Register StrategyGepgPayment in payroll's PaymentsMethodRegistryPoint.

        After registration, a Payroll with payment_method="StrategyGepgPayment"
        uses this strategy for dispatch and reconciliation. Registration is keyed
        by class name (payments_registry/storage.py::get_chosen_payment_method).
        """
        from payroll.payments_registry import PaymentsMethodRegistryPoint
        from gepg_payment_adaptor.strategy import StrategyGepgPayment

        PaymentsMethodRegistryPoint.register_payment_method(
            payment_method_class_list=[StrategyGepgPayment()]
        )
