"""Load MUD-specific protocol strings and line-matching rules from a TOML
file, so adapting the bot to a different MUD doesn't require touching
main.py. See Readme.md for the file format.
"""
import re
import string
import tomllib
from dataclasses import dataclass
from pathlib import Path

_VALID_ACTIONS = ('none', 'login', 'logout')
_VALID_KINDS = ('gossip', 'event')
_OUTBOUND_GOSSIP_FIELDS = {'name', 'text'}


@dataclass
class LoginRules:
    sequence: list[str]
    prompt_end: str
    quit_command: str


@dataclass
class WhoRules:
    start_prefix: str
    entry: re.Pattern


@dataclass
class EventRule:
    name: str
    pattern: re.Pattern
    message: str | None  # Python format template using named groups; None -> relay the whole line
    action: str  # 'none' | 'login' | 'logout' - adds/removes the 'name' group from the online list
    kind: str  # 'gossip' | 'event'


@dataclass
class OutboundRules:
    gossip: str  # Python format template; {name}/{text} are pre-escaped by the caller


@dataclass
class Rules:
    login: LoginRules
    who: WhoRules
    events: list[EventRule]
    outbound: OutboundRules

    @classmethod
    def load(cls, path: str | Path) -> 'Rules':
        path = Path(path)
        try:
            with path.open('rb') as f:
                data = tomllib.load(f)
        except OSError as e:
            raise SystemExit(f'Could not read MUD rules file {path}: {e}')
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f'Invalid TOML in MUD rules file {path}: {e}')

        try:
            login_data = data['login']
            login = LoginRules(
                sequence=list(login_data['sequence']),
                prompt_end=login_data['prompt_end'],
                quit_command=login_data['quit_command'],
            )
            who_data = data['who']
            who = WhoRules(
                start_prefix=who_data['start_prefix'],
                entry=_compile(who_data['entry'], path, 'who.entry'),
            )
        except KeyError as e:
            raise SystemExit(f'MUD rules file {path} is missing required key: {e}')

        events = []
        for i, ev in enumerate(data.get('events', [])):
            try:
                name = ev['name']
                pattern = _compile(ev['pattern'], path, f'events[{i}].pattern')
            except KeyError as e:
                raise SystemExit(f'MUD rules file {path}: events[{i}] is missing required key: {e}')
            message = ev.get('message')
            action = ev.get('action', 'none')
            kind = ev.get('kind', 'event')
            if action not in _VALID_ACTIONS:
                raise SystemExit(
                    f'MUD rules file {path}: events[{i}] ({name!r}) has invalid action {action!r}, '
                    f'expected one of {_VALID_ACTIONS}'
                )
            if kind not in _VALID_KINDS:
                raise SystemExit(
                    f'MUD rules file {path}: events[{i}] ({name!r}) has invalid kind {kind!r}, '
                    f'expected one of {_VALID_KINDS}'
                )
            events.append(EventRule(name=name, pattern=pattern, message=message, action=action, kind=kind))

        outbound_data = data.get('outbound')
        if not isinstance(outbound_data, dict) or 'gossip' not in outbound_data:
            raise SystemExit(f'MUD rules file {path} is missing required key: outbound.gossip')
        gossip_template = outbound_data['gossip']
        _check_template_fields(gossip_template, path, 'outbound.gossip', _OUTBOUND_GOSSIP_FIELDS)
        outbound = OutboundRules(gossip=gossip_template)

        return cls(login=login, who=who, events=events, outbound=outbound)


def _compile(pattern: str, path: Path, where: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as e:
        raise SystemExit(f'MUD rules file {path}: bad regex at {where} ({pattern!r}): {e}')


def _check_template_fields(template: str, path: Path, where: str, allowed: set[str]) -> None:
    try:
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    except ValueError as e:
        raise SystemExit(f'MUD rules file {path}: bad format template at {where} ({template!r}): {e}')
    unknown = fields - allowed
    if unknown:
        raise SystemExit(
            f'MUD rules file {path}: {where} ({template!r}) uses unknown field(s) '
            f'{sorted(unknown)}; allowed: {sorted(allowed)}'
        )
