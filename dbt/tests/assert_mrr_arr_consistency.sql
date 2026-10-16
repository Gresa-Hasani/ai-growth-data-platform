{{ config(severity='error', tags=['revenue']) }}

-- mart_arr.arr must equal mart_mrr.mrr * 12 exactly, per currency - both
-- are built from the identical fact_subscriptions filter, so any
-- difference means the two models' logic has drifted apart.

select
    mart_mrr.currency,
    mart_mrr.mrr,
    mart_arr.arr
from {{ ref('mart_mrr') }}
inner join {{ ref('mart_arr') }} on mart_mrr.currency = mart_arr.currency
where round(mart_mrr.mrr * 12, 2) != round(mart_arr.arr, 2)
