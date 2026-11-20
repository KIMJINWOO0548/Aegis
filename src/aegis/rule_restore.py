"""Offline historical rule restoration proposals; never activate or overwrite."""
import argparse
import json
import sys
from .limit_rules import LimitRuleConfig, LimitRuleError, _digest, load_rule_config
from .rule_versions import (RuleVersionLock, RuleVersionError, load_rule_lock,
                            verify_rule_lock, MAX_LOCK_BYTES)


def restore_rule_versions(current, lock, selections, *, config_version):
    """Create a new config using explicitly selected, already registered snapshots."""
    try:
        # Revalidate even preconstructed models; do not trust mutable nested values.
        current = LimitRuleConfig.model_validate(current.model_dump(mode='json')
                    if isinstance(current, LimitRuleConfig) else current)
        lock = RuleVersionLock.model_validate(lock.model_dump(mode='json')
                    if isinstance(lock, RuleVersionLock) else lock)
        verify_rule_lock(current, lock)
        if (type(selections) is not dict or not selections or
                any(type(k) is not str or type(v) is not str for k,v in selections.items())):
            raise ValueError('invalid selections')
        if type(config_version) is not str or config_version == current.configVersion:
            raise ValueError('new configuration version required')
        ids = {r.ruleId for r in current.rules}
        if not set(selections) <= ids:
            raise ValueError('unknown current rule')
        registered = {(r.ruleId,r.ruleVersion):r.ruleSnapshot for r in lock.rules}
        rules, changes = [], []
        for before in current.rules:
            requested = selections.get(before.ruleId, before.ruleVersion)
            after = registered.get((before.ruleId,requested))
            if after is None:
                raise RuleVersionError('RULE_VERSION_UNREGISTERED', 'Requested rule version is not registered.')
            old, new = before.model_dump(mode='json'), after.model_dump(mode='json')
            rules.append(new)
            if old != new:
                changes.append({'ruleId':before.ruleId, 'fromVersion':before.ruleVersion,
                                'toVersion':requested, 'beforeSha256':_digest(old),
                                'afterSha256':_digest(new),
                                'changedFields':sorted(k for k in old if old[k] != new[k]),
                                'targetChanged':old['target'] != new['target']})
        if not changes:
            raise RuleVersionError('NO_RULE_CHANGE', 'Selection does not change the current rules.')
        candidate = LimitRuleConfig.model_validate({**current.model_dump(mode='json'),
                         'configVersion':config_version, 'rules':rules})
        verify_rule_lock(candidate,lock)
        result = {'schemaVersion':'numeric-rule-restore-proposal-1',
                  'activationStatus':'NOT_APPLIED', 'evaluationMode':'OFFLINE_REVIEW_ONLY',
                  'sourceConfigSha256':_digest(current.model_dump(mode='json')),
                  'referenceLockSha256':_digest(lock.model_dump(mode='json')),
                  'proposedConfigSha256':_digest(candidate.model_dump(mode='json')),
                  'changes':changes, 'proposedConfig':candidate.model_dump(mode='json')}
        if len(json.dumps(result,ensure_ascii=False).encode()) > MAX_LOCK_BYTES:
            raise ValueError('output limit')
        return result
    except RuleVersionError:
        raise
    except (ValueError,TypeError,OverflowError,RecursionError):
        raise RuleVersionError('INVALID_RESTORE_REQUEST','Rule restoration request is invalid.') from None


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise RuleVersionError('INVALID_RESTORE_REQUEST','Rule restoration arguments are invalid.')


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--lock',required=True)
    parser.add_argument('--select',action='append',required=True,metavar='RULE_ID=VERSION')
    parser.add_argument('--new-config-version',required=True)
    try:
        args=parser.parse_args(argv)
        selections={}
        for item in args.select:
            if item.count('=') != 1:
                raise ValueError
            key,value=item.split('=')
            if key in selections:
                raise ValueError
            selections[key]=value
        result=restore_rule_versions(load_rule_config(args.config),load_rule_lock(args.lock),
                                     selections,config_version=args.new_config_version)
        output=json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n'
    except (RuleVersionError,LimitRuleError) as error:
        print(json.dumps({'status':'UNAVAILABLE','code':error.code}),file=sys.stderr)
        return 2
    except ValueError:
        print(json.dumps({'status':'UNAVAILABLE','code':'INVALID_RESTORE_REQUEST'}),file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
