# bifrost/models/review_queue.py
"""Per-tenant shape of the content review queue.

The grid renders one table at a time, which makes an honest review impossible
whenever the thing being reviewed spans two: a reviewer asked to attest that the
correct answer is correct cannot see the answers, only the stem. This queue puts
the parent row and its children on one screen, turns the review columns into
controls, and moves to the next item.

Nothing here knows what a question is. It knows about a parent table with a
status column, a child table joined by a foreign key, some boolean columns that
gate a status transition, and optionally one child column that flags the primary
child. `questions`/`choices`/`is_correct` is one instance of that shape;
`articles`/`revisions` and `products`/`variants` are others.

The same two gates as queue_schema stand between config and SQL, for the same
reason — config reaching an f-string is an injection surface:

  * every identifier is checked against IDENT_RE when the schema is built, and
  * validate() confirms each one exists in the tenant's live schema before the
    config can be saved.

An app with no `review_queue` block gets None, and every caller degrades to the
behaviour that existed before this file. Absent config is not a default config.
"""
from dataclasses import dataclass, field, fields

from .queue_schema import safe_ident


@dataclass(frozen=True)
class ReviewChild:
    """The rows that belong to the record under review.

    `flag` is the column marking the primary child — the correct answer, the
    current revision, the default variant. Optional: a child set with no primary
    is a normal shape, and the queue renders it without the marker.
    """
    table: str
    fk: str
    id: str = 'id'
    columns: tuple = ()
    flag: str = ''
    order_by: str = 'id'


@dataclass(frozen=True)
class ReviewSchema:
    table: str
    id: str = 'id'
    status: str = 'status'
    order_by: str = 'id'

    # Statuses that put a row in the queue, and what the two decisions set.
    awaiting: tuple = ('review',)
    on_approve: str = 'published'
    on_reject: str = 'draft'

    # Columns shown as read-only context, and the booleans the reviewer ticks.
    display: tuple = ()
    controls: tuple = ()

    # First column present in the tenant schema wins; rejection works without one.
    reject_reason: tuple = ('reject_reason', 'notes')

    # Attestation. Written by the server on a review decision, never by the client.
    reviewed_by: str = 'reviewed_by'
    reviewed_at: str = 'reviewed_at'

    child: ReviewChild = None

    @classmethod
    def from_config(cls, cms_config):
        """Builds a schema from an app's cms_config. No block -> None, not defaults.

        There is no sensible default queue: unlike the payment queue, which
        replaced a hardcoded shape, this feature has no prior behaviour to
        preserve. A tenant that has not configured one does not get one.
        """
        block = (cms_config or {}).get('review_queue') or {}
        if not block.get('table'):
            return None
        child_block = block.get('child') or {}
        child = None
        if child_block.get('table') and child_block.get('fk'):
            child = ReviewChild(**_kwargs_for(ReviewChild, child_block))
        schema = cls(**_kwargs_for(cls, block, skip=('child',)), child=child)
        return schema.checked()

    def checked(self):
        for ident in self.identifiers():
            safe_ident(ident)
        if not self.controls:
            raise ValueError("review_queue.controls must name at least one column")
        return self

    def identifiers(self):
        """Every string this schema interpolates into SQL, table names included.

        Status vocabulary is excluded: those travel as bound parameters.
        """
        values = ('awaiting', 'on_approve', 'on_reject')
        out = []
        for obj in (self, self.child):
            if obj is None:
                continue
            for f in fields(obj):
                if f.name in values or f.name == 'child':
                    continue
                value = getattr(obj, f.name)
                if isinstance(value, str) and value:
                    out.append(value)
                elif isinstance(value, (tuple, list)):
                    out.extend(v for v in value if isinstance(v, str) and v)
        return out

    def parent_columns(self):
        """Everything the queue reads from the parent row, deduped, order kept."""
        cols = [self.id, self.status, *self.display, *self.controls]
        seen, out = set(), []
        for c in cols:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def validate(self, cur):
        """Checks this schema against the tenant's live tables.

        Returns human-readable errors, empty when the config is usable. The
        reason column is optional — rejection degrades to no recorded reason
        rather than failing validation.
        """
        from .queue_schema import _table_columns
        errors = []

        def check(table, columns, label):
            present = _table_columns(cur, table)
            if not present:
                errors.append(f"{label}: table '{table}' does not exist in the tenant schema.")
                return None
            for col in columns:
                if col and col not in present:
                    errors.append(f"{label}: '{table}' has no column '{col}'.")
            return present

        present = check(self.table, self.parent_columns() + [self.order_by], "review_queue")
        # The stamp is what makes the attestation worth anything; warn loudly if
        # the tenant's publish constraint expects columns that are not there.
        if present:
            for col in (self.reviewed_by, self.reviewed_at):
                if col and col not in present:
                    errors.append(f"review_queue: '{self.table}' has no column '{col}' "
                                  f"— a review cannot be attributed without it.")
        if self.child:
            cols = [self.child.fk, self.child.id, self.child.order_by,
                    *self.child.columns]
            if self.child.flag:
                cols.append(self.child.flag)
            check(self.child.table, cols, "review_queue.child")
        return errors


def _kwargs_for(cls, block, skip=()):
    """Only the keys this dataclass declares; tuples for the sequence fields."""
    names = {f.name for f in fields(cls)} - set(skip)
    out = {}
    for key, value in (block or {}).items():
        if key not in names:
            continue
        out[key] = tuple(value) if isinstance(value, list) else value
    return out


# -- queries ---------------------------------------------------------------
# Identifiers are interpolated only after safe_ident (enforced in checked(), which
# from_config always calls); every value travels as a bound parameter. Row ids are
# NOT cast to int: a tenant keying content by UUID is the common case for content
# tables, and int() would reject it.

def _cols_sql(columns):
    return ', '.join(f'"{c}"' for c in columns)


def submit(conn, schema, row_id, ticked, decision, actor, reason=None, reason_column=None):
    """Records a review decision. Returns (ok, message).

    Approval requires every configured control to be ticked — checked here rather
    than in the template, because a hidden checkbox is not a gate. The attestation
    columns are written from the session, never from the form.
    """
    if decision not in ('approve', 'reject'):
        return False, f"Unknown review decision: {decision!r}"
    if decision == 'approve' and not all(c in ticked for c in schema.controls):
        missing = [c for c in schema.controls if c not in ticked]
        return False, "Cannot approve until every check is ticked. Missing: " + ", ".join(missing)

    sets, params = [], []
    for col in schema.controls:
        sets.append(f'"{col}" = %s')
        params.append(col in ticked)

    sets.append(f'"{schema.status}" = %s')
    params.append(schema.on_approve if decision == 'approve' else schema.on_reject)

    for col, value in ((schema.reviewed_by, actor), (schema.reviewed_at, None)):
        if not col:
            continue
        sets.append(f'"{col}" = %s' if value is not None else f'"{col}" = NOW()')
        if value is not None:
            params.append(value)

    if decision == 'reject' and reason and reason_column:
        sets.append(f'"{safe_ident(reason_column)}" = %s')
        params.append(reason)

    params.append(row_id)
    with conn.cursor() as cur:
        cur.execute(f'SELECT {_cols_sql(schema.parent_columns())} FROM "{schema.table}" '
                    f'WHERE "{schema.id}" = %s', [row_id])
        before = cur.fetchone()
        if not before:
            return False, "That record no longer exists."
        before = dict(zip(schema.parent_columns(), before))

        cur.execute(f'UPDATE "{schema.table}" SET {", ".join(sets)} WHERE "{schema.id}" = %s',
                    params)
        if not cur.rowcount:
            return False, "That record no longer exists."
    conn.commit()
    return True, before


def children_for(cur, schema, parent_ids, fk_type=None):
    """Children for many parents in one query, grouped by parent id.

    The grid shows a page of rows at a time, so fetching children per row would
    be the N+1 the drawer exists to avoid. Returns {} when there is no child
    config or nothing to look up.

    `fk_type` is the foreign key's Postgres type, from introspection. It matters:
    ids bound from Python strings arrive as text[], and Postgres will not
    implicitly compare uuid = text, so an unqualified ANY() fails outright
    against the UUID keys any Supabase tenant gets by default. Casting the array
    to the column's own type keeps the comparison sargable, so the FK index is
    still used. Without a known type we compare on text, which is always correct
    and merely slower.
    """
    if not schema.child or not parent_ids:
        return {}
    child = schema.child
    cols = [child.fk, child.id, *child.columns] + ([child.flag] if child.flag else [])
    ids = [str(pid) for pid in parent_ids]
    if fk_type:
        match = f'"{child.fk}" = ANY(%s::{safe_ident(fk_type)}[])'
    else:
        match = f'"{child.fk}"::text = ANY(%s)'
    cur.execute(
        f'SELECT {_cols_sql(cols)} FROM "{child.table}" '
        f'WHERE {match} ORDER BY "{child.order_by}"',
        [ids])
    grouped = {}
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        grouped.setdefault(str(record[child.fk]), []).append(record)
    return grouped
