"""Deterministic computation of spell costs with modulated power."""
from decimal import Decimal,InvalidOperation,ROUND_CEILING


def spell_cost(spell_id,max_mana,rules,output_percent=None):
    if type(max_mana) is not int or max_mana<0:
        raise ValueError('Maximum mana must be a non-negative integer.')
    costs=rules['full_power_cost_percent']
    if spell_id not in costs:
        raise ValueError('Spell cost not confirmed: '+spell_id)
    if output_percent is None:
        output_percent=rules['default_output_percent']
    try:
        output=Decimal(str(output_percent))
        base=Decimal(str(costs[spell_id]))
    except InvalidOperation as exc:
        raise ValueError('Invalid power or cost.') from exc
    if not output.is_finite() or not 0<output<=100:
        raise ValueError('Power must be greater than 0 and at most 100%.')
    if not base.is_finite() or not 0<=base<=100:
        raise ValueError('The maximum cost must be between 0 and 100%.')
    free_up_to = Decimal(str(rules.get('free_output_up_to_percent', {}).get(spell_id, 0)))
    if not free_up_to.is_finite() or not 0 <= free_up_to <= 100:
        raise ValueError('Invalid free-use threshold.')
    if output <= free_up_to:
        return 0
    if max_mana == 0 and base > 0:
        raise ValueError('This spell needs a character with a mana reserve.')
    return int((Decimal(max_mana)*base*output/Decimal(10000)).to_integral_value(rounding=ROUND_CEILING))


def cost_table(max_mana,rules):
    outputs=sorted({rules['default_output_percent'],10,25,50,100})
    return {'mana_max':max_mana,'default_power_pct':rules['default_output_percent'],
        'mana_cost_per_power_pct':{str(output):{
            spell:spell_cost(spell,max_mana,rules,output)
            for spell in rules['full_power_cost_percent']} for output in outputs}}
