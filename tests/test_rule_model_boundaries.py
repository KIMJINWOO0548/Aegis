"""Public Python callers must not bypass rule validation via model_copy/construct."""
from datetime import timedelta
from unittest.mock import patch
import pytest
from test_limit_rules import AT, batch, rule, config
from aegis.limit_rules import LimitRule, LimitRuleConfig, LimitRuleError, evaluate_rule, review_archive
from aegis.rule_versions import build_rule_lock, verify_rule_lock, RuleVersionError


@pytest.mark.parametrize('changes', [
    {'lowerBound':None,'upperBound':None}, {'upperBound':True},
    {'maxAgeMs':False}, {'lowerBound':11,'upperBound':10}])
def test_unvalidated_rule_copy_cannot_turn_missing_bounds_into_pass(changes):
    unsafe=LimitRule.model_validate(rule()).model_copy(update=changes)
    with pytest.raises(LimitRuleError):
        evaluate_rule(unsafe,batch(value=999),evaluated_at=AT+timedelta(milliseconds=500))


def test_invalid_configuration_rejected_before_archive_read():
    valid=LimitRuleConfig.model_validate(config())
    unsafe=valid.model_copy(update={'rules':(valid.rules[0],valid.rules[0])})
    with patch('aegis.limit_rules.read_raw_archive') as read:
        with pytest.raises(LimitRuleError):
            review_archive(unsafe,'unused',valid.rules[0].target.deviceId,'unused',AT,AT)
        read.assert_not_called()


def test_duplicate_lock_entries_from_model_copy_are_rejected():
    cfg=LimitRuleConfig.model_validate(config());lock=build_rule_lock(cfg)
    unsafe=lock.model_copy(update={'rules':(lock.rules[0],lock.rules[0])})
    with pytest.raises(RuleVersionError):verify_rule_lock(cfg,unsafe)


def test_invalid_config_identity_from_model_copy_is_rejected():
    cfg=LimitRuleConfig.model_validate(config());lock=build_rule_lock(cfg)
    unsafe=cfg.model_copy(update={'configVersion':''})
    with pytest.raises(RuleVersionError):verify_rule_lock(unsafe,lock)
