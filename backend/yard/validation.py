"""RFID-primary validation, run when a movement closes.

Policy ``RFID_PRIMARY_COUNTS_V4``: distinct registered UID/type counts are the
primary evidence. The ultrasonic passage count is an advisory comparison and can
never, on its own, turn a passing composition into a failure.

Everything here is recomputed from stored events, so a movement can be validated
again at any time. Findings are upserted by ``(code, subject)`` and stale ones
are pruned: revalidation corrects the current picture without losing when a
finding was first seen. Alerts are created once and never deleted, because an
acknowledgement is part of the audit trail.

Inventory transitions are **not** applied here; that is CP-07. Until then the
current inventory row *is* the pre-movement state, which is what the
``UNEXPECTED_EXIT``, ``ENTRY_ALREADY_PRESENT`` and ``DIRECTION_CONFLICT`` checks
read. CP-07 must freeze the pre-movement state before applying transitions, or
revalidation will read its own earlier update.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from . import clock
from .models import (
    Alert,
    Direction,
    InventoryRecord,
    Movement,
    MovementEvent,
    MovementFinding,
    MovementPlan,
    PlanAssignment,
    RollingStock,
)

# Legacy marker: movements closed before validation existed carry this finding.
VALIDATION_PENDING = 'VALIDATION_PENDING'

# --- Finding codes -----------------------------------------------------------

UNKNOWN_RFID = 'UNKNOWN_RFID'
RETIRED_ROLLING_STOCK = 'RETIRED_ROLLING_STOCK'
UNPLANNED_MOVEMENT = 'UNPLANNED_MOVEMENT'
PLAN_AMBIGUOUS = 'PLAN_AMBIGUOUS'
LOCOMOTIVE_MISMATCH = 'LOCOMOTIVE_MISMATCH'
MISSING_LOCOMOTIVE_RFID = 'MISSING_LOCOMOTIVE_RFID'
COUNT_MISMATCH = 'COUNT_MISMATCH'
DUPLICATE_OBSERVATION = 'DUPLICATE_OBSERVATION'
ULTRASONIC_COUNT_MISMATCH = 'ULTRASONIC_COUNT_MISMATCH'
DIRECTION_CONFLICT = 'DIRECTION_CONFLICT'
ENTRY_ALREADY_PRESENT = 'ENTRY_ALREADY_PRESENT'
UNEXPECTED_EXIT = 'UNEXPECTED_EXIT'

FAIL = MovementFinding.Severity.FAIL
WARN = MovementFinding.Severity.WARN


class Composition:
    """Distinct-identity view of one movement's stored events."""

    def __init__(self, events):
        self.rfid_observation_count = 0
        self.passage_count = 0
        # Insertion-ordered so findings follow the order units were read.
        self.observation_counts = {}
        self.stock_by_uid = {}

        for event in events:
            if event.kind == MovementEvent.Kind.PHYSICAL_PASSAGE:
                self.passage_count += 1
                continue

            self.rfid_observation_count += 1
            uid = event.rfid_uid
            self.observation_counts[uid] = (
                self.observation_counts.get(uid, 0) + 1
            )
            self.stock_by_uid.setdefault(uid, event.rolling_stock)

    @property
    def distinct_uids(self):
        return list(self.observation_counts)

    @property
    def duplicate_uids(self):
        return {
            uid: count
            for uid, count in self.observation_counts.items()
            if count > 1
        }

    @property
    def known_uids(self):
        return [uid for uid in self.distinct_uids if self.stock_by_uid[uid]]

    @property
    def unknown_uids(self):
        return [uid for uid in self.distinct_uids if not self.stock_by_uid[uid]]

    def stocks(self, kind):
        """Distinct, active stock of one kind, in first-read order.

        Retired stock is excluded from the composition so it cannot satisfy an
        expected count; it is reported separately as a failure.
        """
        found = []
        for uid in self.known_uids:
            stock = self.stock_by_uid[uid]
            if stock.kind != kind or not stock.is_active:
                continue
            if stock not in found:
                found.append(stock)
        return found

    @property
    def retired(self):
        return [
            self.stock_by_uid[uid]
            for uid in self.known_uids
            if not self.stock_by_uid[uid].is_active
        ]


def _finding(code, severity, subject, message, **details):
    return {
        'code': code,
        'severity': severity,
        'subject': subject or '',
        'message': message,
        'details': details,
    }


def _counts_text(counts):
    return (
        f"{counts['locos']} loco(s), {counts['coaches']} coach(es), "
        f"{counts['wagons']} wagon(s)"
    )


# --- Plan resolution ---------------------------------------------------------

def eligible_plans(movement):
    """Plans that may legitimately bind to this movement.

    Same station and direction, still open for matching, within the configured
    time window, and not already bound to a different movement. The movement's
    own plan is always re-admitted: it is ``COMPLETED`` after the first
    validation, and dropping it would unbind the plan on revalidation.
    """
    window = timedelta(
        minutes=getattr(settings, 'PLAN_MATCH_WINDOW_MINUTES', 60)
    )

    bound_elsewhere = (
        Movement.objects.filter(plan__isnull=False)
        .exclude(pk=movement.pk)
        .values_list('plan_id', flat=True)
    )

    statuses = (MovementPlan.Status.SCHEDULED, MovementPlan.Status.ACTIVE)

    queryset = MovementPlan.objects.filter(
        station_id=movement.station_id,
        direction=movement.direction,
        expected_start__gte=movement.started_at - window,
        expected_start__lte=movement.started_at + window,
    ).exclude(pk__in=bound_elsewhere)

    if movement.plan_id:
        queryset = queryset.filter(
            Q(status__in=statuses) | Q(pk=movement.plan_id)
        )
    else:
        queryset = queryset.filter(status__in=statuses)

    return list(
        queryset.select_related('expected_lead_loco').order_by(
            'expected_start', 'pk'
        )
    )


def _nearest(plans, started_at):
    return min(
        plans,
        key=lambda plan: (
            abs((plan.expected_start - started_at).total_seconds()),
            plan.pk,
        ),
    )


def loco_roster(plan):
    """Locomotives the plan expects *by identity*, lead first.

    Locomotive identities live on the plan, not on the locomotive master: the
    same unit hauls different rakes on different days, so a boolean on
    ``RollingStock`` could not answer "is this the locomotive for *this*
    movement". Additional locomotives are listed through the same
    ``PlanAssignment`` association that already carries coaches and wagons.

    The roster may be shorter than ``expected_locos``; callers decide what that
    incomplete identity claim permits.
    """
    roster = []

    if plan.expected_lead_loco_id:
        roster.append(plan.expected_lead_loco)

    if plan.expected_locos > len(roster):
        for assignment in plan.assignments.select_related('rolling_stock').order_by('pk'):
            stock = assignment.rolling_stock
            if stock.kind != RollingStock.Kind.LOCO:
                continue
            if stock not in roster:
                roster.append(stock)

    return roster


def resolve_plan(movement, composition):
    """Bind at most one plan, recording how it was identified.

    Direct match on the expected lead locomotive comes first. Only when that
    fails is the coach/wagon assignment fallback used, and only when the usable
    assignments all point at a single eligible plan.
    """
    candidates = eligible_plans(movement)
    result = {
        'plan': None,
        'source': Movement.MatchSource.NONE,
        'ambiguous': [],
        'candidates': candidates,
    }

    observed_loco_ids = {
        stock.pk for stock in composition.stocks(RollingStock.Kind.LOCO)
    }
    direct = [
        plan
        for plan in candidates
        if plan.expected_lead_loco_id in observed_loco_ids
    ]

    if direct:
        chosen = _nearest(direct, movement.started_at) if len(direct) > 1 else direct[0]
        result['plan'] = chosen
        result['source'] = Movement.MatchSource.LOCOMOTIVE_RFID
        if len(direct) > 1:
            result['ambiguous'] = [plan.pk for plan in direct]
        return result

    observed_ids = {
        stock.pk
        for kind in (
            RollingStock.Kind.LOCO,
            RollingStock.Kind.COACH,
            RollingStock.Kind.WAGON,
        )
        for stock in composition.stocks(kind)
    }
    candidate_ids = [plan.pk for plan in candidates]

    if observed_ids and candidate_ids:
        assignment_plan_ids = sorted(
            set(
                PlanAssignment.objects.filter(
                    rolling_stock_id__in=observed_ids,
                    plan_id__in=candidate_ids,
                ).values_list('plan_id', flat=True)
            )
        )
        if len(assignment_plan_ids) == 1:
            plan_id = assignment_plan_ids[0]
            result['plan'] = next(
                plan for plan in candidates if plan.pk == plan_id
            )
            result['source'] = Movement.MatchSource.COACH_ASSIGNMENT
            return result
        if len(assignment_plan_ids) > 1:
            result['ambiguous'] = assignment_plan_ids

    return result


# --- Findings ----------------------------------------------------------------

def _inventory_states(movement, uids):
    return dict(
        InventoryRecord.objects.filter(
            station_id=movement.station_id,
            rfid_uid__in=uids,
        ).values_list('rfid_uid', 'status')
    )


def build_findings(movement, composition, resolution):
    plan = resolution['plan']
    findings = []

    for uid in composition.distinct_uids:
        stock = composition.stock_by_uid[uid]
        if stock is None:
            findings.append(
                _finding(
                    UNKNOWN_RFID,
                    FAIL,
                    uid,
                    f'UID {uid} is not registered to any rolling stock.',
                    rfid_uid=uid,
                )
            )

    for stock in composition.retired:
        findings.append(
            _finding(
                RETIRED_ROLLING_STOCK,
                FAIL,
                stock.number,
                f'{stock.number} is marked inactive and cannot be counted in a '
                'consist.',
                number=stock.number,
                rfid_uid=stock.rfid_uid,
            )
        )

    for uid, count in composition.duplicate_uids.items():
        findings.append(
            _finding(
                DUPLICATE_OBSERVATION,
                WARN,
                uid,
                f'UID {uid} was read {count} times. It counts as one unit; the '
                'repeated observations are kept as evidence.',
                rfid_uid=uid,
                observations=count,
                counted_as=1,
            )
        )

    if resolution['ambiguous']:
        findings.append(
            _finding(
                PLAN_AMBIGUOUS,
                WARN,
                '',
                'Several eligible plans match the observed units, so no plan '
                'was bound. Releasing the wrong plan or adding assignments '
                'resolves this.',
                candidate_plans=resolution['ambiguous'],
            )
        )

    if plan is None:
        findings.append(
            _finding(
                UNPLANNED_MOVEMENT,
                FAIL,
                '',
                'No eligible plan could be matched to this movement.',
                candidate_plan_count=len(resolution['candidates']),
                unique_rfid_count=len(composition.distinct_uids),
                passage_count=composition.passage_count,
            )
        )
    else:
        expected = {
            'locos': plan.expected_locos,
            'coaches': plan.expected_coaches,
            'wagons': plan.expected_wagons,
        }
        observed = {
            'locos': len(composition.stocks(RollingStock.Kind.LOCO)),
            'coaches': len(composition.stocks(RollingStock.Kind.COACH)),
            'wagons': len(composition.stocks(RollingStock.Kind.WAGON)),
        }
        if observed != expected:
            findings.append(
                _finding(
                    COUNT_MISMATCH,
                    FAIL,
                    '',
                    f'Observed {_counts_text(observed)} against plan '
                    f'{plan.plan_code} expecting {_counts_text(expected)}.',
                    expected=expected,
                    observed=observed,
                    expected_total=plan.expected_total,
                    observed_total=sum(observed.values()),
                )
            )

        roster = loco_roster(plan)
        roster_ids = {stock.pk for stock in roster}
        observed_locoes = composition.stocks(RollingStock.Kind.LOCO)
        observed_loco_ids = {stock.pk for stock in observed_locoes}
        observed_loco_numbers = sorted(stock.number for stock in observed_locoes)

        for stock in roster:
            if stock.pk in observed_loco_ids:
                continue
            role = 'lead locomotive' if stock.pk == plan.expected_lead_loco_id else 'locomotive'
            findings.append(
                _finding(
                    MISSING_LOCOMOTIVE_RFID,
                    FAIL,
                    stock.number,
                    f'Expected {role} {stock.number} ({stock.rfid_uid}) was not '
                    'read. Its identity is missing evidence, not proof that it '
                    'did not pass.',
                    expected_number=stock.number,
                    expected_rfid_uid=stock.rfid_uid,
                    expected_role=role,
                    observed_locomotives=observed_loco_numbers,
                )
            )

        # Identity is only claimed when the plan names every locomotive it
        # expects. A plan that says "2 locomotives" but names only the lead has
        # not said what the second one is, so a second, different locomotive is
        # legitimate and is checked by COUNT_MISMATCH instead of being failed.
        identity_complete = bool(roster) and len(roster) >= plan.expected_locos
        if identity_complete:
            unexpected = [
                stock for stock in observed_locoes if stock.pk not in roster_ids
            ]
            if unexpected:
                findings.append(
                    _finding(
                        LOCOMOTIVE_MISMATCH,
                        FAIL,
                        unexpected[0].number if len(unexpected) == 1 else '',
                        'Observed locomotive(s) are not in the locomotive list '
                        f'expected by plan {plan.plan_code}; the fallback did not '
                        'conceal this conflict.',
                        expected_numbers=[stock.number for stock in roster],
                        observed_numbers=[stock.number for stock in unexpected],
                        plan=plan.plan_code,
                    )
                )

    known = composition.known_uids
    if known:
        states = _inventory_states(movement, known)

        if movement.direction == Direction.ENTRY:
            already_present = [
                uid
                for uid in known
                if states.get(uid) == InventoryRecord.Status.IN_YARD
            ]
            for uid in already_present:
                findings.append(
                    _finding(
                        ENTRY_ALREADY_PRESENT,
                        WARN,
                        uid,
                        f'UID {uid} was already recorded in the yard. Presence '
                        'is unchanged.',
                        rfid_uid=uid,
                    )
                )
            if len(already_present) * 2 > len(known):
                findings.append(
                    _finding(
                        DIRECTION_CONFLICT,
                        WARN,
                        '',
                        'Most observed units are already recorded as in the '
                        'yard, which suggests the lane mode does not match the '
                        'physical movement. Inventory is not auto-flipped.',
                        already_present=len(already_present),
                        observed_known=len(known),
                        direction=movement.direction,
                    )
                )
        else:
            for uid in known:
                if states.get(uid) != InventoryRecord.Status.IN_YARD:
                    findings.append(
                        _finding(
                            UNEXPECTED_EXIT,
                            FAIL,
                            uid,
                            f'UID {uid} was never recorded in the yard, so this '
                            'exit is unexpected.',
                            rfid_uid=uid,
                        )
                    )

    distinct = len(composition.distinct_uids)
    if composition.passage_count != distinct:
        difference = composition.passage_count - distinct
        if difference > 0:
            meaning = (
                'more interruptions than identities: possible missed RFID, '
                'untagged stock, a repeat passage or sensor noise'
            )
        else:
            meaning = (
                'fewer interruptions than identities: possible merged or '
                'missed passages, or a tag presented without a passage'
            )
        details = {
            'passage_count': composition.passage_count,
            'unique_rfid_count': distinct,
            'difference': difference,
        }
        if plan is not None:
            details['expected_total'] = plan.expected_total
        findings.append(
            _finding(
                ULTRASONIC_COUNT_MISMATCH,
                WARN,
                '',
                f'{composition.passage_count} passage(s) against {distinct} '
                f'distinct UID(s): {meaning}. This is advisory evidence and '
                'does not change the RFID-based outcome.',
                **details,
            )
        )

    return findings


def outcome_for(composition, findings):
    if not composition.known_uids:
        return Movement.Outcome.UNVERIFIED
    if any(item['severity'] == FAIL for item in findings):
        return Movement.Outcome.FLAGGED
    return Movement.Outcome.VALID


# --- Persistence -------------------------------------------------------------

def _bind_plan(movement, resolution):
    plan = resolution['plan']

    movement.plan = plan
    movement.plan_match_source = resolution['source']
    movement.expected_lead_loco = plan.expected_lead_loco if plan else None
    movement.expected_locos = plan.expected_locos if plan else None
    movement.expected_coaches = plan.expected_coaches if plan else None
    movement.expected_wagons = plan.expected_wagons if plan else None

    if plan is not None and plan.status == MovementPlan.Status.SCHEDULED:
        MovementPlan.objects.filter(pk=plan.pk).update(
            status=MovementPlan.Status.ACTIVE,
            updated_at=clock.server_now(),
        )
        plan.status = MovementPlan.Status.ACTIVE


def _store_findings(movement, findings):
    kept = []

    for item in findings:
        finding, _created = MovementFinding.objects.update_or_create(
            movement=movement,
            code=item['code'],
            subject=item['subject'],
            defaults={
                'severity': item['severity'],
                'message': item['message'],
                'details': item['details'],
            },
        )
        kept.append(finding.pk)

    # Drop findings that no longer apply, including the legacy placeholder.
    # ``exclude(pk__in=[])`` is a no-op in Django, so an empty result needs an
    # explicit delete-everything.
    stale = MovementFinding.objects.filter(movement=movement)
    if kept:
        stale = stale.exclude(pk__in=kept)
    stale.delete()


def _store_alerts(movement, findings):
    """Create one alert per finding. Existing alerts are never duplicated.

    Alerts are notifications with an acknowledgement trail, so revalidation
    adds and updates nothing; it does not erase an acknowledged notification
    just because the underlying finding was later cleared.
    """
    for item in findings:
        if item['severity'] == MovementFinding.Severity.INFO:
            continue
        Alert.objects.get_or_create(
            movement=movement,
            code=item['code'],
            subject=item['subject'],
            defaults={
                'station_id': movement.station_id,
                'severity': (
                    Alert.Severity.CRITICAL
                    if item['severity'] == FAIL
                    else Alert.Severity.WARNING
                ),
                'message': item['message'],
                'details': item['details'],
                'raised_at': clock.server_now(),
            },
        )


@transaction.atomic
def validate_movement(movement):
    """Validate one closed movement and store its outcome.

    Recomputable: calling it twice produces the same findings and outcome, and
    does not duplicate alerts or introduce a second plan binding.
    """
    movement = (
        Movement.objects.select_for_update()
        .select_related('device', 'plan', 'station')
        .get(pk=movement.pk)
    )

    if movement.state != Movement.State.CLOSED:
        raise ValueError('Only a closed movement can be validated.')

    events = list(
        movement.events.select_related('rolling_stock').order_by(
            'received_at', 'id'
        )
    )
    composition = Composition(events)
    resolution = resolve_plan(movement, composition)
    findings = build_findings(movement, composition, resolution)

    _bind_plan(movement, resolution)

    movement.validation_policy = getattr(
        settings,
        'VALIDATION_POLICY',
        movement.validation_policy,
    )
    movement.validation_result = outcome_for(composition, findings)
    movement.validated_at = clock.server_now()
    movement.save(
        update_fields=(
            'plan',
            'plan_match_source',
            'expected_lead_loco',
            'expected_locos',
            'expected_coaches',
            'expected_wagons',
            'validation_policy',
            'validation_result',
            'validated_at',
            'updated_at',
        )
    )

    _store_findings(movement, findings)
    _store_alerts(movement, findings)

    if movement.plan_id:
        MovementPlan.objects.filter(
            pk=movement.plan_id,
            status=MovementPlan.Status.ACTIVE,
        ).update(
            status=MovementPlan.Status.COMPLETED,
            updated_at=clock.server_now(),
        )

    return movement
