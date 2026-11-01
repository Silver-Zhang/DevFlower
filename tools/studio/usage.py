"""Whitelisted provider-reported accounting, never arbitrary protocol metadata."""
import math


def public_usage(value):
    if not isinstance(value,dict):return None
    def number(v):
        return 0<=v<=2**63-1 if type(v) is int else type(v) is float and v>=0 and math.isfinite(v)
    fields=value.get('usage',{})
    names=('input_tokens','output_tokens','cached_input_tokens','cache_read_input_tokens','cache_creation_input_tokens')
    tokens={k:v for k,v in fields.items() if k in names and number(v)} if isinstance(fields,dict) else {}
    cost=value.get('total_cost_usd')
    return dict(semantics=value.get('semantics') if value.get('semantics') in ('turn','session-cumulative-do-not-sum') else 'unknown',
                usage=tokens,total_cost_usd=cost if number(cost) else 'unknown')
