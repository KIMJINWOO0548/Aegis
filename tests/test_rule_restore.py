import copy
import json
import pytest
from test_rule_versions import config, rule, write
from aegis.rule_versions import build_rule_lock, RuleVersionError, verify_rule_lock
from aegis.rule_restore import restore_rule_versions, main


def history():
    old=config()
    current=config([rule('2',upperBound=20)],version='2')
    return old,current,build_rule_lock(current,build_rule_lock(old))


def test_restore_historical_snapshot_into_new_config_without_mutation():
    old,current,lock=history()
    before=current.model_dump(mode='json'); reference=lock.model_dump(mode='json')
    result=restore_rule_versions(current,lock,{'numeric.bounds':'1'},config_version='3')
    assert result['activationStatus']=='NOT_APPLIED'
    assert result['proposedConfig']['rules']==old.model_dump(mode='json')['rules']
    assert result['proposedConfig']['configVersion']=='3'
    assert result['changes'][0]['changedFields']==['ruleVersion','upperBound']
    verify_rule_lock(result['proposedConfig'],lock)
    assert current.model_dump(mode='json')==before and lock.model_dump(mode='json')==reference


@pytest.mark.parametrize('selection,version', [({},'3'),({'missing':'1'},'3'),
    ({'numeric.bounds':'unknown'},'3'),({'numeric.bounds':'1'},'2'),
    ({'numeric.bounds':'2'},'3'),({'numeric.bounds':'1'},'bad version')])
def test_reject_ambiguous_unregistered_or_noop_restore(selection,version):
    _,current,lock=history()
    with pytest.raises(RuleVersionError):
        restore_rule_versions(current,lock,selection,config_version=version)


def test_unselected_rules_and_target_change_are_explicit():
    old=config([rule(),rule(ruleId='other')])
    current=config([rule('2',target={'deviceId':'CHANGED','signalKey':'numeric.alpha'}),
                    rule(ruleId='other')],version='2')
    lock=build_rule_lock(current,build_rule_lock(old))
    result=restore_rule_versions(current,lock,{'numeric.bounds':'1'},config_version='3')
    assert result['changes'][0]['targetChanged']
    assert result['proposedConfig']['rules'][1]==current.rules[1].model_dump(mode='json')


def test_tampered_reference_and_current_conflict_fail():
    _,current,lock=history()
    bad=lock.model_dump(mode='json');bad['rules'][0]['ruleSnapshot']['upperBound']=999
    with pytest.raises(RuleVersionError):
        restore_rule_versions(current,bad,{'numeric.bounds':'1'},config_version='3')
    changed=current.model_dump(mode='json');changed['rules'][0]['upperBound']=99
    with pytest.raises(RuleVersionError,match='conflict'):
        restore_rule_versions(changed,lock,{'numeric.bounds':'1'},config_version='3')


def test_cli_stdout_only_and_duplicate_selection_rejected(tmp_path,capsys):
    _,current,lock=history();c=tmp_path/'current.json';l=tmp_path/'lock.json'
    write(c,current.model_dump(mode='json'));write(l,lock.model_dump(mode='json'))
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    args=['--config',str(c),'--lock',str(l),'--select','numeric.bounds=1','--new-config-version','3']
    assert main(args)==0
    assert json.loads(capsys.readouterr().out)['activationStatus']=='NOT_APPLIED'
    assert main(args+['--select','numeric.bounds=2'])==2
    output=capsys.readouterr();assert not output.out
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
