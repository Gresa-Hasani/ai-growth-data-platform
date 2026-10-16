# Business Metrics Reference

Precise formulas for every metric exposed by the Phase 5 marts. See
[dbt_marts.md](dbt_marts.md) for the full rationale, limitations, and
verified example values behind each of these; this page is the quick
lookup.

| Metric | Formula | Model | Grain | Notes |
|---|---|---|---|---|
| MRR | `sum(monthly_equivalent_amount)` where `is_subscription_recurring_active` | `mart_mrr` | (as_of_date, currency) | Point-in-time snapshot, not a historical series |
| ARR | `MRR * 12`, same eligible set | `mart_arr` | (as_of_date, currency) | Never derived from annual invoice totals |
| ARPU | `MRR / count(distinct paying organizations)` | `mart_arpu` | (as_of_date, currency) | Per organization, not per user; not average invoice value |
| Customer churn rate | `customers_churned / customers_active_at_start` | `mart_customer_churn` | (period_start, period_end, organization_type) | Customer = organization; 30-day trailing window |
| Gross revenue churn rate | `churned_recurring_revenue / opening_recurring_revenue` | `mart_revenue_churn` | (period_start, period_end, currency) | Gross only - no net/expansion calculation |
| Realized LTV | `sum(paid_amount)` to date | `mart_customer_ltv` | (organization_key, currency) | Observed historical fact, not a forecast |
| Estimated LTV | `ARPU / platform_churn_rate` | `mart_customer_ltv` | (organization_key, currency) | Speculative projection; NULL if churn_rate is 0/unavailable |
| Trial-to-paid conversion | `count(is_converted) / count(is_observation_complete)` | `mart_trial_conversion` | per user (compute the rate yourself from this grain) | Excludes trials still within the 30-day observation window from the denominator |
| Adoption rate | `adopting_user_count / eligible_user_count` | `mart_feature_adoption` | per event_name | Distinct users, not raw event volume |
| Retention rate | `retained_users / cohort_size` | `mart_retention_cohorts` | (cohort_month, period_index) | Only observable periods included |
| API error rate | `error_request_count / request_count` | `fact_api_usage`, `mart_api_usage` | varies | Via `safe_divide` - NULL, not an error, when request_count is 0 |

## Definitions

**MRR (Monthly Recurring Revenue)**: the sum of monthly-equivalent
recurring amounts across subscriptions currently generating revenue
(`status = 'active'`, or `'canceled'` with a still-future `cancelled_at`).
Annual subscriptions are normalized by dividing by 12. A snapshot as of
today - not reconstructable as a historical series from this source (see
dbt_marts.md).

**ARR (Annualized Recurring Revenue)**: MRR × 12, for the identical
eligible-subscription set. Not a sum of annual invoice totals.

**ARPU (Average Revenue Per User)**: here, average revenue **per paying
organization** - current MRR divided by the count of organizations with at
least one active recurring subscription. Distinct from average invoice
value (which would include one-time billing-cycle timing noise).

**Customer churn**: customers (organizations) active at the start of a
30-day period who have no remaining active recurring subscription by the
end of that period, divided by customers active at the start. An
organization with multiple subscriptions is not churned while any one of
them remains active.

**Gross revenue churn**: recurring revenue lost (from subscriptions that
were active at period start but aren't by period end) divided by recurring
revenue at period start. Does not net against expansion/new revenue - net
revenue churn is not implemented (no historical subscription-amount data
exists to detect expansion/contraction against).

**LTV (Lifetime Value)**: two distinct numbers, reported separately -
realized (historical, observed) and estimated (a speculative ARPU/churn
projection). Never combined into one "the" LTV figure.

**Trial conversion**: a user "converts" when a paid (non-missing, non-free
plan) subscription starts at or after their trial's start. Users whose
trial began less than 30 days ago are excluded from the rate's denominator
entirely - they are not yet a known outcome, not a failure.

**Feature adoption**: a user has "adopted" a feature if they generated at
least one event of that type, ever. The adoption rate is adopting users
divided by all users, not event volume divided by anything.

**Retention**: a user is "retained" in cohort month + N if they generated
at least one product event in that later calendar month. Only months that
have actually occurred are reported.
