{% macro is_subscription_recurring_active(status_column, cancelled_at_column) %}
{#-
    "Currently generating recurring revenue, right now" - used consistently
    by mart_mrr/mart_arr/mart_customer_churn so the active-subscription
    definition never drifts between them.

    Two cases count as active:
      - status = 'active'
      - status = 'canceled' BUT cancelled_at is still in the future.
        Verified in the dev dataset: ~38% of "canceled" subscriptions have
        a cancelled_at timestamp after the current time (111 of 295) - this
        represents a *scheduled* cancellation (e.g. "cancel at period end"),
        not one that has actually taken effect yet. Treating these as
        inactive would understate today's real recurring revenue.

    Excluded, and documented as deliberate: 'trialing' (no revenue yet),
    'past_due' (payment has already failed - conservative exclusion, since
    whether that revenue will actually be collected is genuinely uncertain),
    'canceled' with a past cancelled_at (already lapsed), and 'unknown'.
-#}
(
    {{ status_column }} = 'active'
    or ({{ status_column }} = 'canceled' and {{ cancelled_at_column }} > current_timestamp)
)
{% endmacro %}
