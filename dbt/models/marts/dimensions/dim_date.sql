-- Deterministic date spine covering the actual supported range in the
-- dataset (not a fixed/hardcoded range, and no external calendar data
-- source) - bounds are computed from the real min/max timestamps across
-- every business-dated source, extended to current_date so "today" is
-- always covered for point-in-time marts (mart_mrr, mart_customer_churn)
-- even if it's later than any historical event. cancelled_at can be
-- future-dated (scheduled cancellations - see
-- is_subscription_recurring_active macro), so it's included in the upper
-- bound too.

with bounds as (

    select
        least(
            (select min(signup_at) from {{ ref('stg_app__users') }}),
            (select min(started_at) from {{ ref('stg_billing__subscriptions') }}),
            (select min(issued_at) from {{ ref('stg_billing__invoices') }}),
            (select min(event_timestamp) from {{ ref('stg_product__events') }}),
            (select min(request_timestamp) from {{ ref('stg_api__usage') }})
        )::date as min_date,
        greatest(
            (select max(cancelled_at) from {{ ref('stg_billing__subscriptions') }}),
            (select max(current_period_end) from {{ ref('stg_billing__subscriptions') }}),
            (select max(issued_at) from {{ ref('stg_billing__invoices') }}),
            (select max(event_timestamp) from {{ ref('stg_product__events') }}),
            (select max(request_timestamp) from {{ ref('stg_api__usage') }}),
            current_timestamp
        )::date as max_date

),

spine as (

    select generate_series(bounds.min_date, bounds.max_date, interval '1 day')::date as date_day
    from bounds

)

select
    date_day,
    extract(year from date_day)::int as year_number,
    extract(quarter from date_day)::int as quarter_number,
    extract(month from date_day)::int as month_number,
    date_trunc('month', date_day)::date as month_start_date,
    to_char(date_day, 'YYYY-MM') as month_label,
    extract(week from date_day)::int as week_number,
    extract(isodow from date_day)::int as day_of_week_number,
    (extract(isodow from date_day) in (6, 7)) as is_weekend
from spine
