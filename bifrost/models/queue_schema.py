# bifrost/models/queue_schema.py
"""Per-tenant shape of the manual payment queue.

The queue's plumbing is generic — row locking, the state machine, duplicate
detection, the SLA clock, the audit trail. Only the identifiers were not:
`payments`, `entitlements`, `exam_track_id` and friends were literals. They now
come from the app's `cms_config.payment_queue` block.

Two gates stand between config and SQL and both are required:

  * every identifier is checked against IDENT_RE when the schema is built, and
  * validate() confirms each one exists in the tenant's live schema before the
    config can be saved.

Config becomes an injection surface the moment it reaches an f-string, so neither
gate is optional.

An app with no `payment_queue` block gets the defaults below, which are the shape
that used to be hardcoded. Ministry Exam Prep is therefore configured by omission
and its SQL is byte-identical to the pre-config build.
"""
import re
from dataclasses import dataclass, field, fields, replace

IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def safe_ident(name):
    """Validates a SQL identifier. Raises rather than asserts — asserts vanish under -O."""
    if not IDENT_RE.match(str(name or '')):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


@dataclass(frozen=True)
class Subject:
    """Who paid. Joined onto the queue for the label, and suspendable from the console."""
    table: str = 'users'
    id: str = 'id'
    label: str = 'email'
    status: str = 'status'
    suspended_at: str = 'suspended_at'
    suspend_reason: str = 'suspend_reason'


@dataclass(frozen=True)
class Grant:
    """What approving actually gives the payer.

    One row per (subject, scope) carrying a status — the entitlement shape. A tenant
    whose grant is a shipment, a stock movement or a partial refund does NOT fit this
    and must leave `grant` unset: approval then only settles the payment row and the
    webhook hands the decision back to the tenant's own app, which is where stock and
    fulfilment logic belongs.
    """
    table: str = 'entitlements'
    subject_key: str = 'user_id'
    scope_key: str = 'exam_track_id'
    status: str = 'status'
    activated_at: str = 'activated_at'
    on_approve: str = 'premium'
    on_revoke: str = 'rejected'
    statuses: tuple = ('free', 'pending', 'premium', 'rejected')


@dataclass(frozen=True)
class ScopeOptions:
    """Rows behind the approve dropdown. Never hard-coded options."""
    table: str = 'exam_tracks'
    id: str = 'id'
    label: str = 'name_en'
    group: str = 'ministry'
    active: str = 'is_active'


@dataclass(frozen=True)
class QueueSchema:
    table: str = 'payments'
    id: str = 'id'
    subject_key: str = 'user_id'
    amount: str = 'amount'
    reference: str = 'txn_ref'
    receipt: str = 'receipt_url'
    checksum: str = 'receipt_checksum'
    created: str = 'created_at'
    status: str = 'status'
    reviewed_at: str = 'reviewed_at'
    reviewed_by: str = 'reviewed_by'
    scope: str = 'exam_track_id'

    # First column present in the tenant schema wins; the queue works without either.
    reject_reason: tuple = ('reject_reason', 'notes')
    refund_reason: tuple = ('refund_reason', 'notes')

    # Tenant vocabulary. `transitions` is the server-side state machine; `settled`
    # is what makes a reference count as already spent.
    open_states: tuple = ('pending', 'unclaimed')
    settled: tuple = ('approved', 'refunded')
    transitions: dict = field(default_factory=lambda: {
        'pending': {'approved', 'rejected'},
        'unclaimed': {'approved', 'rejected'},
        'approved': {'refunded'},
        'rejected': set(),
        'refunded': set(),
    })
    # Console action -> the tenant's word for the resulting status.
    actions: dict = field(default_factory=lambda: {
        'approve': 'approved', 'reject': 'rejected', 'refund': 'refunded',
    })

    subject: Subject = field(default_factory=Subject)
    grant: Grant = field(default_factory=Grant)
    scope_options: ScopeOptions = field(default_factory=ScopeOptions)

    # -- construction -----------------------------------------------------
    @classmethod
    def from_config(cls, cms_config):
        """Builds a schema from an app's cms_config. Missing block -> the defaults."""
        block = (cms_config or {}).get('payment_queue') or {}
        schema = cls(**_kwargs_for(cls, block, nested={
            'subject': Subject, 'grant': Grant, 'scope_options': ScopeOptions,
        }))
        return schema.checked()

    def checked(self):
        """Validates every identifier. Called for you by from_config()."""
        for ident in self.identifiers():
            safe_ident(ident)
        return self

    def identifiers(self):
        """Every string this schema will interpolate into SQL, table names included."""
        out = []
        for obj in (self, self.subject, self.grant, self.scope_options):
            if obj is None:
                continue
            for f in fields(obj):
                if f.name in ('transitions', 'actions', 'open_states', 'settled', 'statuses',
                              'on_approve', 'on_revoke'):
                    continue  # values, not identifiers — these travel as bound params or
                              # as quoted literals built from the state config
                value = getattr(obj, f.name)
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, (tuple, list)):
                    out.extend(v for v in value if isinstance(v, str))
        return out

    def without_grant(self):
        """A queue that settles payments but grants nothing — the webhook-only shape."""
        return replace(self, grant=None)

    # -- SQL fragments ----------------------------------------------------
    def status_for(self, action):
        """Tenant status literal for a console action. Raises on an unknown action."""
        try:
            return self.actions[action]
        except KeyError:
            raise ValueError(f"No status configured for action: {action!r}")

    def settled_sql(self):
        """The settled-status set, as a SQL literal list."""
        return ', '.join(f"'{s}'" for s in self.settled)

    def is_open(self, status):
        return (status or '').lower() in self.open_states

    # -- schema validation ------------------------------------------------
    def validate(self, cur):
        """Checks this schema against the tenant's live tables.

        Returns a list of human-readable errors, empty when the config is usable.
        Optional columns (the reason columns, and anything the queue already degrades
        without) are not required to exist.
        """
        errors = []

        def check(table, columns, label, required=True):
            present = _table_columns(cur, table)
            if not present:
                errors.append(f"{label}: table '{table}' does not exist in the tenant schema.")
                return
            for col in columns:
                if col and col not in present:
                    errors.append(f"{label}: '{table}' has no column '{col}'.")

        check(self.table, [self.id, self.subject_key, self.amount, self.reference,
                           self.receipt, self.status], "payment_queue")
        check(self.subject.table, [self.subject.id, self.subject.label], "payment_queue.subject")
        if self.grant:
            check(self.grant.table,
                  [self.grant.subject_key, self.grant.scope_key,
                   self.grant.status, self.grant.activated_at], "payment_queue.grant")
        if self.scope_options:
            check(self.scope_options.table,
                  [self.scope_options.id, self.scope_options.label],
                  "payment_queue.scope_options")

        for state, targets in self.transitions.items():
            for target in targets:
                if target not in self.transitions:
                    errors.append(
                        f"payment_queue.states: '{state}' can move to '{target}', "
                        f"which is not itself a declared state."
                    )
        for action, status in self.actions.items():
            if status not in self.transitions:
                errors.append(
                    f"payment_queue.states: action '{action}' produces status "
                    f"'{status}', which is not a declared state."
                )
        return errors


def _kwargs_for(cls, block, nested=None):
    """Config keys that match dataclass fields, nested objects built recursively."""
    nested = nested or {}
    names = {f.name for f in fields(cls)}
    out = {}
    for key, value in block.items():
        if key not in names:
            continue  # unknown keys are ignored, not an error — forward compatibility
        if key in nested:
            out[key] = None if value is None else nested[key](**_kwargs_for(nested[key], value))
        elif key in ('open_states', 'settled'):
            out[key] = tuple(value)
        elif key == 'transitions':
            out[key] = {k: set(v) for k, v in value.items()}
        elif key in ('reject_reason', 'refund_reason'):
            out[key] = tuple(value) if isinstance(value, (list, tuple)) else (value,)
        else:
            out[key] = value
    return out


def _table_columns(cur, table_name):
    """Introspected column list for a table. Empty list if the table does not exist."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        [table_name]
    )
    return [r[0] for r in cur.fetchall()]


DEFAULT = QueueSchema()
