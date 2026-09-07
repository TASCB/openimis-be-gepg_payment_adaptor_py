# openIMIS Backend GePG payment adaptor

Registers `StrategyGepgPayment` into payroll's `PaymentsMethodRegistryPoint`, so a
Payroll with `payment_method = "StrategyGepgPayment"` dispatches through GePG.

**GePG is reached over GovESB**, not by direct REST. This module therefore has no
connector and no transaction-log model: it publishes through the shared
`coremis_app_integration.govesb.GovESBProducer`, and delivery audit belongs to
`coremis_app_integration` (see `docs/PAYMENT_INTEGRATION_ROADMAP.md`).

> The payroll strategy path and the TASAF paylist path are two separate routes to a
> gateway. For TASAF disbursement the **paylist path is authoritative** — see track B1
> in the roadmap. Do not enable both against the same payroll.

## Configuration

The GovESB topic is `tasaf.payment.instruction.gepg`. Map it to the api_code issued by
e-GA in `settings.ESB["API_CODES"]`; without a mapping the topic is sent as-is.
