"""The types the pipeline passes from one stage to the next.

Config, things read from the client files, and the case file for a run.
``to_dict`` writes the same JSON shape as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

# Where a fact came from: the report request, the meeting note, or the account database.
SourceRole = Literal["request", "meeting", "db"]
# What a file in the client folder is. noise and internal are left out of the report.
FileRole = Literal["request", "meeting", "db", "noise", "internal"]
# What a money figure means. Moving £20,000 is a transfer, not a new balance.
MoneyKind = Literal[
    "account_balance",
    "transfer_amount",
    "received_proceeds",
    "loan_repayment",
    "contingent_proceeds",
]
# A view over a money kind. Extraction still stores the kind.
MoneyRole = Literal["balance", "movement", "other"]
MoneyAvailability = Literal["available", "unavailable", "contingent", "unknown"]
# Who fills a <<slot>> in the template: fixed code, or the model.
SlotSource = Literal["render", "llm"]
# The recommendation slot is given the actions, not the raw facts.
SlotInput = Literal["actions"]
# Whether we read the file. Images are skipped.
ParseStatus = Literal["parsed", "skipped"]

# Fields we trust the report request for.
RequestField = Literal[
    "accounts_covered",
    "investment_amount",
    "source_of_funds",
    "selling",
    "product",
    "ownership",
    "risk_profile",
    "initial_charge",
]
# Fields we trust the meeting note for.
MeetingField = Literal["circumstances", "objectives", "recommendation_summary"]
# Columns on an account, other than its value.
AccountAttr = Literal["type", "owner", "platform", "status", "currency"]
# Those columns as observation field names, for example account_type.
DbMetaField = Literal[
    "account_type",
    "account_owner",
    "account_platform",
    "account_status",
    "account_currency",
]
# The balance, and the date of the database snapshot. The newest dated balance wins.
DbField = Literal["account_value", "snapshot_date"]
FactField = RequestField | MeetingField | DbMetaField | DbField

CORE_ROLES: frozenset[SourceRole] = frozenset(get_args(SourceRole))
FILE_ROLES: frozenset[FileRole] = frozenset(get_args(FileRole))
MONEY_KINDS: frozenset[MoneyKind] = frozenset(get_args(MoneyKind))
MONEY_ROLES: frozenset[MoneyRole] = frozenset(get_args(MoneyRole))
MONEY_AVAILABILITY: frozenset[MoneyAvailability] = frozenset(get_args(MoneyAvailability))


@dataclass(frozen=True)
class MoneyView:
    """Role and availability projected from a stored money kind."""

    role: MoneyRole
    availability: MoneyAvailability


_MONEY_VIEWS: dict[MoneyKind, MoneyView] = {
    "account_balance": MoneyView("balance", "unknown"),
    "transfer_amount": MoneyView("movement", "unknown"),
    "received_proceeds": MoneyView("other", "available"),
    "loan_repayment": MoneyView("other", "unavailable"),
    "contingent_proceeds": MoneyView("other", "contingent"),
}


def project_money(kind: MoneyKind | None) -> MoneyView | None:
    """Map a stored kind onto role and availability. Unknown kinds stay unprojected."""
    if kind not in _MONEY_VIEWS:
        return None
    return _MONEY_VIEWS[kind]
REQUEST_FIELDS: frozenset[RequestField] = frozenset(get_args(RequestField))
MEETING_FIELDS: frozenset[MeetingField] = frozenset(get_args(MeetingField))
ACCOUNT_ATTRS: frozenset[AccountAttr] = frozenset(get_args(AccountAttr))
DB_META_FIELDS: frozenset[DbMetaField] = frozenset(get_args(DbMetaField))
ACCOUNT_FIELD: dict[AccountAttr, DbMetaField] = {
    "type": "account_type",
    "owner": "account_owner",
    "platform": "account_platform",
    "status": "account_status",
    "currency": "account_currency",
}


@dataclass
class Observation:
    """One thing read from a source file, with the file it came from."""

    field: str
    value: Any
    source_role: SourceRole
    source_file: str
    as_of: str | None = None
    account_id: str | None = None
    quote: str | None = None
    approximate: bool | None = None
    kind: MoneyKind | None = None

    def money_kind(self) -> MoneyKind | None:
        if self.kind in MONEY_KINDS:
            return self.kind
        if self.field == "account_value":
            return "account_balance"
        if self.field == "investment_amount":
            return "transfer_amount"
        return None

    def group_key(self) -> tuple[str, str | None, str | None]:
        account_id = str(self.account_id) if self.account_id else None
        return self.field, account_id, self.money_kind()


@dataclass
class TypedSource:
    """A client file after we have decided what it is and read it."""

    path: Path
    name: str
    text: str
    role: FileRole

    @classmethod
    def from_mapping(cls, role: str, data: dict[str, Any] | TypedSource) -> TypedSource:
        if isinstance(data, TypedSource):
            return data
        path = data.get("path")
        return cls(
            path=path if isinstance(path, Path) else Path(str(path or data.get("name", role))),
            name=str(data.get("name", role)),
            text=str(data.get("text", "")),
            role=role,  # type: ignore[arg-type]
        )


@dataclass
class DbAccount:
    """One account from the database file. If an id appears twice, the first one is kept."""

    account_id: str
    type: Any = None
    owner: Any = None
    platform: Any = None
    status: Any = None
    currency: Any = None
    value: Any = None
    valuation_date: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DbAccount | None:
        account_id = data.get("account_id")
        if not account_id:
            return None
        return cls(
            account_id=str(account_id),
            type=data.get("type"),
            owner=data.get("owner"),
            platform=data.get("platform"),
            status=data.get("status"),
            currency=data.get("currency"),
            value=data.get("value"),
            valuation_date=data.get("valuation_date"),
        )


@dataclass
class KnownAccount:
    """An account we already know, shown to the meeting extract so it cannot invent an id."""

    account_id: str
    type: Any = None
    platform: Any = None
    owner: Any = None

    def line(self) -> str:
        return (
            f"- {self.account_id}: type={self.type!r}, "
            f"platform={self.platform!r}, owner={self.owner!r}"
        )


@dataclass
class LlmObservation:
    """One item the extract model returned, before we decide whether to keep it."""

    field: str
    value: Any = None
    account_id: str | None = None
    as_of: str | None = None
    approximate: bool | None = None
    kind: MoneyKind | None = None
    quote: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LlmObservation | None:
        field = data.get("field")
        if not field:
            return None
        return cls(
            field=str(field),
            value=data.get("value"),
            account_id=None if data.get("account_id") is None else str(data.get("account_id")),
            as_of=None if data.get("as_of") is None else str(data.get("as_of")),
            approximate=data.get("approximate") if isinstance(data.get("approximate"), bool) else (
                bool(data["approximate"]) if data.get("approximate") is not None else None
            ),
            kind=data.get("kind") if data.get("kind") in MONEY_KINDS else None,
            quote=None if data.get("quote") is None else str(data.get("quote")),
        )


@dataclass
class MeetingExtract:
    meeting_date: str | None
    observations: list[LlmObservation]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MeetingExtract:
        raw_date = payload.get("meeting_date")
        observations = []
        for item in payload.get("observations") or []:
            if isinstance(item, dict):
                parsed = LlmObservation.from_dict(item)
                if parsed is not None:
                    observations.append(parsed)
        return cls(
            meeting_date=str(raw_date) if raw_date else None,
            observations=observations,
        )


@dataclass
class FileClassification:
    name: str
    role: FileRole

    @classmethod
    def list_from_payload(cls, payload: dict[str, Any]) -> list[FileClassification]:
        out = []
        for item in payload.get("classifications") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            role = item.get("role")
            if isinstance(name, str) and role in FILE_ROLES:
                out.append(cls(name=name, role=role))
        return out


@dataclass
class SourcedValue:
    """The value we kept for a field, and which source it came from."""

    value: Any
    source: SourceRole | None
    source_file: str | None
    as_of: str | None = None
    account_id: str | None = None
    conflict: bool = False
    kind: MoneyKind | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "value": self.value,
            "source": self.source,
            "source_file": self.source_file,
            "as_of": self.as_of,
            "conflict": self.conflict,
        }
        if self.account_id is not None:
            data["account_id"] = self.account_id
        if self.kind is not None:
            data["kind"] = self.kind
        return data


@dataclass
class ValueAlternate:
    value: Any
    source_role: SourceRole | None
    source_file: str | None
    as_of: str | None
    quote: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source_role": self.source_role,
            "source_file": self.source_file,
            "as_of": self.as_of,
            "quote": self.quote,
        }


@dataclass
class Account:
    """A client account. Only the database can create one."""

    account_id: str
    type: Any = None
    type_source: SourceRole | None = None
    owner: Any = None
    owner_source: SourceRole | None = None
    platform: Any = None
    platform_source: SourceRole | None = None
    status: Any = None
    status_source: SourceRole | None = None
    currency: Any = None
    currency_source: SourceRole | None = None
    value: Any = None
    value_source: SourceRole | None = None
    value_source_file: str | None = None
    as_of: str | None = None
    value_conflict: bool = False
    needs_review: bool = False
    approximate: bool = False
    synthetic: bool = False
    value_alternates: list[ValueAlternate] = field(default_factory=list)
    _assigned: set[str] = field(default_factory=set, repr=False)

    def set_meta(self, name: str, value: Any, source: SourceRole | None) -> None:
        if name not in ACCOUNT_ATTRS:
            raise ValueError(f"Unknown account attribute {name!r}")
        setattr(self, name, value)
        setattr(self, f"{name}_source", source)
        self._assigned.add(name)
        self._assigned.add(f"{name}_source")

    def set_value(
        self,
        *,
        value: Any,
        source: SourceRole | None,
        source_file: str | None,
        as_of: str | None,
        conflict: bool = False,
        approximate: bool = False,
        needs_review: bool = False,
        alternates: list[ValueAlternate] | None = None,
    ) -> None:
        self.value = value
        self.value_source = source
        self.value_source_file = source_file
        self.as_of = as_of
        self.value_conflict = conflict
        self._assigned.update(
            {"value", "value_source", "value_source_file", "as_of", "value_conflict"}
        )
        if approximate:
            self.approximate = True
            self._assigned.add("approximate")
        if needs_review:
            self.needs_review = True
            self._assigned.add("needs_review")
        if alternates:
            self.value_alternates = alternates
            self._assigned.add("value_alternates")

    def mark_synthetic(self) -> None:
        self.synthetic = True
        self._assigned.add("synthetic")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"account_id": self.account_id}
        for key in (
            "currency",
            "currency_source",
            "owner",
            "owner_source",
            "platform",
            "platform_source",
            "status",
            "status_source",
            "type",
            "type_source",
            "value",
            "value_source",
            "value_source_file",
            "as_of",
            "value_conflict",
            "needs_review",
            "approximate",
            "synthetic",
        ):
            if key not in self._assigned:
                continue
            data[key] = getattr(self, key)
        if "value_alternates" in self._assigned and self.value_alternates:
            data["value_alternates"] = [item.to_dict() for item in self.value_alternates]
        return data


@dataclass
class Conflict:
    field: str
    details: str
    observations: list[Observation] = field(default_factory=list)

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "details": self.details}


@dataclass
class RecordedFact:
    """The value we kept, plus every reading that supported it, so the case file can cite them."""

    field: str
    account_id: str | None
    kind: MoneyKind | None
    draft: Observation
    group: list[Observation]
    conflict: bool


@dataclass
class ReconciledFacts:
    selling: bool
    conflicts: list[Conflict]
    review_items: list[str]
    facts: dict[str, SourcedValue]
    accounts: list[Account]
    observation_count: int
    recorded: list[RecordedFact] = field(default_factory=list)

    def fact_value(self, name: str, default: str = "") -> Any:
        entry = self.facts.get(name)
        if entry is None:
            return default
        return entry.value

    def add_review_tags(self, tags: list[str]) -> ReconciledFacts:
        items = list(self.review_items)
        seen = set(items)
        for tag in tags:
            if tag not in seen:
                items.append(tag)
                seen.add(tag)
        return ReconciledFacts(
            selling=self.selling,
            conflicts=self.conflicts,
            review_items=items,
            facts=self.facts,
            accounts=self.accounts,
            observation_count=self.observation_count,
            recorded=self.recorded,
        )


@dataclass
class Evidence:
    source_file: str | None
    source_role: SourceRole | None
    value: Any
    kind: MoneyKind | None
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_role": self.source_role,
            "value": self.value,
            "kind": self.kind,
            "excerpt": self.excerpt,
        }


@dataclass
class CaseFact:
    id: str
    field: str
    value: Any
    source_file: str | None
    excerpt: str
    conflict: bool
    kind: MoneyKind | None
    account_id: str | None
    evidence: list[Evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "field": self.field,
            "value": self.value,
            "source_file": self.source_file,
            "excerpt": self.excerpt,
            "conflict": self.conflict,
            "kind": self.kind,
            "account_id": self.account_id,
            "evidence": [row.to_dict() for row in self.evidence],
        }


DecisionType = Literal["contribute", "transfer", "dispose", "retain", "confirm"]
DecisionStatus = Literal["agreed", "outstanding"]


@dataclass
class MeetingDecision:
    """A non-money decision named in the meeting note, before it is stored on the case."""

    type: DecisionType
    status: DecisionStatus
    quote: str
    source_file: str
    subject: str | None = None
    target_account_id: str | None = None


@dataclass
class Decision:
    """A choice recorded from evidence. It is not a money kind and not a recommendation action."""

    id: str
    type: DecisionType
    status: DecisionStatus
    supports: str
    target_account_id: str | None = None
    subject: str | None = None
    amount: Any = None
    amount_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "supports": self.supports,
        }
        if self.target_account_id:
            data["target_account_id"] = self.target_account_id
        if self.subject:
            data["subject"] = self.subject
        if self.amount is not None or self.amount_status == "not_agreed":
            data["amount"] = self.amount
        if self.amount_status and self.amount_status != "agreed":
            data["amount_status"] = self.amount_status
        return data


@dataclass
class RecommendationAction:
    """One recommendation. amount is the figure to write; it is not a money kind."""

    id: str
    amount: Any
    supports: str
    who: str
    product: Any
    source_of_funds: Any
    summary: Any
    corroborated: str | None = None
    amount_status: str = "agreed"

    def line(self) -> str:
        return (
            f"- id={self.id} amount={self.amount!r} amount_status={self.amount_status!r} "
            f"who={self.who!r} product={self.product!r} source_of_funds={self.source_of_funds!r} "
            f"summary={self.summary!r} supports={self.supports!r}"
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "amount": self.amount,
            "supports": self.supports,
            "who": self.who,
            "product": self.product,
            "source_of_funds": self.source_of_funds,
            "summary": self.summary,
        }
        if self.corroborated is not None:
            data["corroborated"] = self.corroborated
        if self.amount_status != "agreed":
            data["amount_status"] = self.amount_status
        return data


@dataclass
class NarrativeCitation:
    fact_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"fact_ids": self.fact_ids}


@dataclass
class RecommendationItem:
    action_id: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"action_id": self.action_id, "text": self.text}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecommendationItem | None:
        if not isinstance(data, dict):
            return None
        return cls(
            action_id=str(data.get("action_id") or ""),
            text=str(data.get("text") or "").strip(),
        )


@dataclass
class RecommendationDraft:
    """What the model returns for the recommendation slot: one sentence per action."""

    items: list[RecommendationItem]

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> RecommendationDraft:
        raw = (payload or {}).get("items") or []
        items = []
        for item in raw:
            parsed = RecommendationItem.from_dict(item) if isinstance(item, dict) else None
            if parsed is not None:
                items.append(parsed)
        return cls(items=items)


@dataclass
class NarrativeDraft:
    """What the model returns for a written slot: the sentence, and the fact ids it used."""

    text: str
    fact_ids: list[str]

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> NarrativeDraft | None:
        if not payload or "text" not in payload:
            return None
        return cls(
            text=str(payload.get("text") or "").strip(),
            fact_ids=[str(item) for item in payload.get("fact_ids") or []],
        )


@dataclass
class RecommendationSection:
    items: list[RecommendationItem]

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}


@dataclass
class SourceRecord:
    file: str
    role: FileRole
    status: ParseStatus

    def to_dict(self) -> dict[str, str]:
        return {"file": self.file, "role": self.role, "status": self.status}


@dataclass
class RunHeader:
    label: str
    recorded_at: str
    model: str
    config_sha: str
    git_rev: str

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "recorded_at": self.recorded_at,
            "model": self.model,
            "config_sha": self.config_sha,
            "git_rev": self.git_rev,
        }


@dataclass
class CaseDocument:
    """Everything one run keeps: sources, facts, actions, and which facts each section used."""

    run: RunHeader | None
    sources: list[SourceRecord]
    facts: list[CaseFact]
    actions: list[RecommendationAction]
    decisions: list[Decision]
    sections: dict[str, NarrativeCitation | RecommendationSection]
    selling: bool | None
    conflicts: list[Conflict]
    review_items: list[str]
    accounts: list[Account]

    def record_narrative(self, name: str, fact_ids: list[str]) -> None:
        self.sections[name] = NarrativeCitation(fact_ids=fact_ids)

    def record_recommendation(self, items: list[RecommendationItem]) -> None:
        self.sections["recommendation"] = RecommendationSection(items=items)

    def facts_for(self, fields: list[str] | None) -> list[CaseFact]:
        if not fields:
            return list(self.facts)
        wanted = set(fields)
        return [fact for fact in self.facts if fact.field in wanted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict() if self.run else {},
            "sources": [row.to_dict() for row in self.sources],
            "facts": [fact.to_dict() for fact in self.facts],
            "actions": [action.to_dict() for action in self.actions],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "sections": {name: section.to_dict() for name, section in self.sections.items()},
            "selling": self.selling,
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "review_items": list(self.review_items),
            "accounts": [account.to_dict() for account in self.accounts],
        }


@dataclass(frozen=True)
class Always:
    def applies(self, facts: ReconciledFacts) -> bool:
        del facts
        return True


@dataclass(frozen=True)
class FactEquals:
    """Include this section when a yes/no fact matches, for example selling is true."""

    fact: str
    equals: bool
    description: str = ""

    def applies(self, facts: ReconciledFacts) -> bool:
        actual = getattr(facts, self.fact, None)
        return bool(actual) == bool(self.equals)


@dataclass(frozen=True)
class LegacySellingPhrase:
    """Older configs say when to include a section in plain words, such as 'when selling'."""

    text: str

    def applies(self, facts: ReconciledFacts) -> bool:
        lowered = self.text.lower()
        if "sold" in lowered or "selling" in lowered:
            return bool(facts.selling)
        raise ValueError(f"Unknown use_if rule: {self.text!r}")


InclusionRule = Always | FactEquals | LegacySellingPhrase


def inclusion_rule(raw: Any) -> InclusionRule:
    if raw is None or raw == "always":
        return Always()
    if isinstance(raw, dict):
        return FactEquals(
            fact=str(raw.get("fact", "selling")),
            equals=bool(raw.get("equals", True)),
            description=str(raw.get("description") or ""),
        )
    if isinstance(raw, str):
        return LegacySellingPhrase(raw)
    raise ValueError(f"Unknown use_if rule: {raw!r}")


@dataclass(frozen=True)
class PlaceholderSpec:
    prompt: str
    source: SlotSource | None = None
    facts: tuple[str, ...] = ()
    input: SlotInput | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaceholderSpec:
        source = data.get("source")
        if source not in {None, "render", "llm"}:
            raise ValueError(f"Unknown placeholder source: {source!r}")
        slot_input = data.get("input")
        if slot_input not in {None, "actions"}:
            raise ValueError(f"Unknown placeholder input: {slot_input!r}")
        return cls(
            prompt=str(data.get("prompt") or ""),
            source=source,
            facts=tuple(data.get("facts") or ()),
            input=slot_input,
        )

    def is_render(self, name: str, render_names: frozenset[str]) -> bool:
        if self.source == "render":
            return True
        if self.source == "llm":
            return False
        return name in render_names

    def is_actions(self) -> bool:
        return self.input == "actions"


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    use_if: InclusionRule
    template: str
    placeholders: dict[str, PlaceholderSpec]

    def applies(self, facts: ReconciledFacts) -> bool:
        return self.use_if.applies(facts)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SectionSpec:
        placeholders = {
            name: PlaceholderSpec.from_dict(spec)
            for name, spec in (data.get("placeholders") or {}).items()
        }
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            use_if=inclusion_rule(data.get("use_if", "always")),
            template=str(data.get("template") or ""),
            placeholders=placeholders,
        )


@dataclass(frozen=True)
class TemplateConfig:
    document_title: str
    global_instructions: str
    sections: tuple[SectionSpec, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateConfig:
        return cls(
            document_title=str(data.get("document_title") or "Advice Report"),
            global_instructions=str(data.get("global_instructions") or ""),
            sections=tuple(SectionSpec.from_dict(section) for section in data.get("sections") or []),
        )

    def document_dict(self) -> dict[str, str]:
        """The title, in the shape the document formatter expects."""
        return {"document_title": self.document_title}
