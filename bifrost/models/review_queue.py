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


# Rendering contracts for the material a record is checked against. A citation
# is a short reference shown beside the record; a passage is quoted text shown as
# a block; a document is a pointer rendered as a link when it is one. Deliberately
# a closed set — an open-ended "render this however" would be a template language.
EVIDENCE_ROLES = ('citation', 'passage', 'document')


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
class Annotations:
    """Spans stored against a text column of the record under review.

    A term link, a citation range, a redaction — anything that says "characters
    start..end of this column mean something". They are the reason editing that
    column is not a free action: every edit before a span shifts it, and a shifted
    span is not detectably wrong, it is quietly wrong.

    Offsets are code point indices with an exclusive end — `text[start:end]` is
    the annotated surface. That matches Python slicing and, for text in the basic
    plane, what a browser counting UTF-16 units would see.
    """
    table: str
    fk: str
    start: str
    end: str
    target: str          # the text column the offsets index into
    surface: str = ''    # optional column holding the expected text of the span


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

    # What the record is checked AGAINST. Entries are {column, role, label?};
    # roles are rendering contracts, not data types.
    evidence: tuple = ()

    # Spans into one of the record's text columns, if the tenant keeps any.
    annotations: Annotations = None

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
        ann_block = block.get('annotations') or {}
        annotations = None
        if all(ann_block.get(k) for k in ('table', 'fk', 'start', 'end', 'target')):
            annotations = Annotations(**_kwargs_for(Annotations, ann_block))
        schema = cls(**_kwargs_for(cls, block, skip=('child', 'annotations')),
                     child=child, annotations=annotations)
        return schema.checked()

    def checked(self):
        for ident in self.identifiers():
            safe_ident(ident)
        if not self.controls:
            raise ValueError("review_queue.controls must name at least one column")
        for item in self.evidence:
            if not isinstance(item, dict) or not item.get('column'):
                raise ValueError(f"review_queue.evidence entry needs a column: {item!r}")
            if item.get('role') not in EVIDENCE_ROLES:
                raise ValueError(
                    f"review_queue.evidence role must be one of "
                    f"{', '.join(sorted(EVIDENCE_ROLES))}: {item.get('role')!r}")
        return self

    def evidence_columns(self):
        return [item['column'] for item in self.evidence if item.get('column')]

    def identifiers(self):
        """Every string this schema interpolates into SQL, table names included.

        Status vocabulary is excluded: those travel as bound parameters.
        """
        values = ('awaiting', 'on_approve', 'on_reject')
        out = list(self.evidence_columns())
        for obj in (self, self.child, self.annotations):
            if obj is None:
                continue
            for f in fields(obj):
                if f.name in values or f.name in ('child', 'evidence', 'annotations'):
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

        present = check(self.table,
                        self.parent_columns() + [self.order_by] + self.evidence_columns(),
                        "review_queue")
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


def pending_count(cur, schema):
    cur.execute(f'SELECT COUNT(*) FROM "{schema.table}" WHERE "{schema.status}" = ANY(%s)',
                [list(schema.awaiting)])
    return cur.fetchone()[0]


def next_awaiting(cur, schema, after_id=None):
    """The next record awaiting review, as a dict, or None when the queue is empty.

    Ordered by the configured order_by so traversal is stable, and skipping
    `after_id` so a record the reviewer just decided on cannot be handed back
    when its new status still matches `awaiting` — a send-back that lands in a
    tenant's own awaiting set would otherwise loop on the same record forever.
    """
    columns = schema.parent_columns() + schema.evidence_columns()
    seen, ordered = set(), []
    for c in columns:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    sql = (f'SELECT {_cols_sql(ordered)} FROM "{schema.table}" '
           f'WHERE "{schema.status}" = ANY(%s)')
    params = [list(schema.awaiting)]
    if after_id is not None:
        sql += f' AND "{schema.id}"::text <> %s'
        params.append(str(after_id))
    sql += f' ORDER BY "{schema.order_by}" LIMIT 1'
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(zip(ordered, row)) if row else None


def restore(conn, schema, row_id, before):
    """Puts a record back as it was before a decision. One level, no history.

    Undo is what lets a reviewer move quickly: without it people hesitate on
    every record. The previous values come from the server's own read, never
    from the client, so undo cannot be used to write an arbitrary state.
    """
    columns = [schema.status, *schema.controls]
    if schema.reviewed_by:
        columns.append(schema.reviewed_by)
    if schema.reviewed_at:
        columns.append(schema.reviewed_at)
    columns = [c for c in columns if c in before]
    if not columns:
        return False
    sets = ', '.join(f'"{c}" = %s' for c in columns)
    params = [before[c] for c in columns] + [row_id]
    with conn.cursor() as cur:
        cur.execute(f'UPDATE "{schema.table}" SET {sets} WHERE "{schema.id}" = %s', params)
        changed = cur.rowcount
    conn.commit()
    return bool(changed)


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
    # The before-state includes the attestation columns, so undo can put back who
    # had signed it — restoring the status but leaving a stale reviewer would be
    # worse than not undoing at all.
    before_cols = schema.parent_columns() + [c for c in (schema.reviewed_by, schema.reviewed_at)
                                             if c and c not in schema.parent_columns()]
    with conn.cursor() as cur:
        cur.execute(f'SELECT {_cols_sql(before_cols)} FROM "{schema.table}" '
                    f'WHERE "{schema.id}" = %s', [row_id])
        before = cur.fetchone()
        if not before:
            return False, "That record no longer exists."
        before = dict(zip(before_cols, before))

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


def span_check(cur, schema, row_id, new_text, fk_type=None):
    """Would writing `new_text` invalidate this record's spans? Returns a reason, or None.

    Two failures, and they need different words. Drift means the stored spans no
    longer describe the text that is already there — something changed them
    behind the check, and re-running the tenant's extractor is the fix. Shift
    means the edit in hand would move them, which is the ordinary case: a
    reviewer fixing one word early in a sentence silently relocates every span
    after it.

    Nothing here recomputes spans. Matching is the tenant's algorithm and their
    lexicon, and a platform guessing at it would produce annotations nobody
    asked for. This only answers whether the existing ones survive.
    """
    ann = schema.annotations
    if not ann or new_text is None:
        return None

    cast = f'::{safe_ident(fk_type)}' if fk_type else ''
    cols = [ann.start, ann.end] + ([ann.surface] if ann.surface else [])
    cur.execute(
        f'SELECT {_cols_sql(cols)} FROM "{ann.table}" WHERE "{ann.fk}" = %s{cast}',
        [str(row_id)])
    spans = cur.fetchall()
    if not spans:
        return None

    cur.execute(f'SELECT "{ann.target}" FROM "{schema.table}" WHERE "{schema.id}" = %s',
                [row_id])
    current = cur.fetchone()
    current = current[0] if current else None
    if current is None or current == new_text:
        return None

    # Drift check first: if the spans do not describe the CURRENT text, the row
    # is already broken and the reviewer's edit is not the cause.
    if ann.surface:
        for start, end, surface in spans:
            if surface is None:
                continue
            if current[start:end] != surface:
                return (f"This record's {len(spans)} term spans no longer match "
                        f"'{ann.target}' as stored — the row is already out of "
                        f"sync. Re-run the extractor before editing.")

    return (f"'{ann.target}' carries {len(spans)} term spans with character "
            f"offsets. Editing the text moves every span after the edit, and "
            f"nothing downstream would detect it. Clear the spans or re-run the "
            f"extractor for this record, then edit.")
