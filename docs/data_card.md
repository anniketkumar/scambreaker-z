# Data card

## 1. Base data: IBM AMLworld, HI-Small (real labels)

| | |
|---|---|
| Source | IBM Research, *Realistic Synthetic Financial Transactions for Anti-Money Laundering Models* (Altman et al., NeurIPS 2023 Datasets & Benchmarks) |
| Distribution | Kaggle: [IBM Transactions for Anti Money Laundering (AML)](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml), file `HI-Small_Trans.csv` |
| Licence | Community Data License Agreement (see the Kaggle page) |
| Size | ~5.08M transfers, ~515K accounts, 1–10 Sep 2022 (plus a short tail), ~0.1% laundering |
| Columns used | timestamp, sending/receiving bank + account, amounts and currencies, payment format, `Is Laundering` |
| Where it lives | Raw CSV and every derived file sit on the LUKS2-encrypted `/secure` volume on the LinuxONE VM; nothing is committed to git |

Preparation (`scambreaker/data/prep.py`):
- Amounts are converted to USD using exchange rates derived from the file's own cross-currency trades.
- Account keys (`bank:account`) are mapped to integer IDs.
- Rows are sorted by time and split by day: days 1–6 train, days 7–8 validation, days 9 onwards test.

## 2. Injected scam episodes (synthetic, labelled)

Banks do not publish victim-side scam data, so APP-scam episodes are simulated and injected into the real AMLworld stream (`scambreaker/data/inject.py`, fixed seed):

- **Victims** are real AMLworld accounts with retail-like activity (3–200 digital outgoing transfers) that are never involved in laundering. Each victim is used for at most one episode, and victims in different splits are disjoint.
- **Payees** are real AMLworld accounts that receive laundering transfers in the same period (money mules), so the payee-side signal comes from real data.
- **Five typologies**, with shapes loosely following Singapore Police Force descriptions:

  | Typology | Payments | Amounts (× victim's usual payment) | Gaps | Rails |
  |---|---|---|---|---|
  | Government official impersonation | 1–3 | ~8× | 5–90 min | ACH / Wire |
  | Investment | 3–8 | escalating ×1.3–2.2 | 2–24 h | Bitcoin / ACH / Wire |
  | Job / task | 4–12 | small, escalating ×1.2–1.8 | 10–120 min | ACH |
  | Fake friend / romance | 1–4 | ~3× | 1–24 h | ACH / Wire |
  | E-commerce | 1 | ~0.8× | – | ACH |

- **Session (coercion) signals** are attached to every digital transfer, legitimate or scam: on a call, unknown caller, remote-access app, minutes since payee added, session length, hesitation, failed attempts, new device. They are drawn from **overlapping** distributions; for example 6% of legitimate transfers happen during a call and 12% of investment-scam payments do. No single signal decides the outcome.
- **Hard negatives:** large first-time payments to legitimate payees (rent, fees, purchases), with slightly elevated hesitation and call rates.
- **Demo personas** (Mdm Tan, Ravi, Aisha) get synthetic histories only; their scenario transfers are triggered live in the demo and are not part of any metric.

Every injected row carries `kind != 0`. Every scam metric is reported as "on simulated episodes injected into IBM AMLworld data". Only model A is evaluated on AMLworld's own (real) labels.

## 3. Known limitations

- Coercion-signal distributions are assumptions informed by public scam advisories, not measured bank telemetry. A bank would refit them on its own data; the architecture does not change.
- AMLworld is itself simulated, although it is a published benchmark built to be realistic.
- The test period is two days. Results are a credible signal, not a production estimate.
- **Hour of day:** AMLworld spreads activity evenly over 24 hours, while injected episodes follow typology-specific hours (more evening activity). The distributions overlap heavily, but this is a mild simulation difference the behaviour model can see.
- **Leakage check performed:** a first training run let scam Bitcoin payments be cross-currency while every real AMLworld Bitcoin transfer is same-currency, a synthetic fingerprint. Injected payments now take the cross-currency rate observed in the real data for that rail, and all reported numbers come from the corrected run.
