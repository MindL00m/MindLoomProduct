/**
 * Directory CSV mapping + validation.
 *
 * Columns map to the expanded Neo4j `Person` node. System-managed fields
 * (person_id, canonical_email, canonical_name, manager_id, created_at,
 * updated_at, last_active, source_ids) are derived server-side and are NOT part
 * of the CSV.
 *
 *   name           REQUIRED  -> Person.name
 *   email          REQUIRED  -> Person.email (canonical_email = lower(email))
 *   user_id        optional  -> Person.user_id
 *   preferred_name optional  -> Person.preferred_name
 *   photo_url      optional  -> Person.photo_url
 *   title          optional  -> Person.title
 *   department     optional  -> Person.department
 *   business_unit  optional  -> Person.business_unit
 *   employee_type  optional  -> Person.employee_type
 *   status         optional  -> Person.status (active | inactive, default active)
 *   manager_email  optional  -> reporting hierarchy (REPORTS_TO)
 *   teams          optional  -> Person.groups (";" or "," separated)
 *   org_unit       optional  -> Person.org_unit
 *   location       optional  -> Person.location
 *   city           optional  -> Person.city
 *   country        optional  -> Person.country
 *   desk_location  optional  -> Person.desk_location
 *   start_date     optional  -> Person.start_date (ISO 8601)
 *
 * Header matching is case-insensitive and accepts common aliases.
 */

import { parseCsv } from "./csv";

export type PersonStatus = "active" | "inactive";

export interface DirectoryPerson {
  // Identity
  name: string;
  email: string;
  userId?: string;
  preferredName?: string;
  photoUrl?: string;
  // Employment
  title?: string;
  department?: string;
  businessUnit?: string;
  employeeType?: string;
  status: PersonStatus;
  // Organization
  managerEmail?: string;
  teams: string[];
  orgUnit?: string;
  // Location
  location?: string;
  city?: string;
  country?: string;
  deskLocation?: string;
  // Dates
  startDate?: string;
}

/** Optional, non-collection scalar string fields settable straight from a cell. */
type ScalarField = Exclude<
  keyof DirectoryPerson,
  "name" | "email" | "status" | "teams"
>;

export type IssueSeverity = "error" | "warning";

export interface RowIssue {
  /** 1-based row number as it appears in the file (header = row 1). */
  row: number;
  field: string;
  message: string;
  severity: IssueSeverity;
}

export interface DirectoryParseResult {
  people: DirectoryPerson[];
  issues: RowIssue[];
  headers: string[];
  /** Required columns that were absent from the header row (blocks import). */
  missingRequiredColumns: string[];
  /** Header names present in the file that we don't recognise (kept for info). */
  unknownColumns: string[];
  totalRows: number;
}

type FieldKey =
  | "name"
  | "email"
  | "status"
  | "teams"
  | ScalarField;

/** Canonical field -> accepted header aliases (all compared lower-cased). */
const FIELD_ALIASES: Record<FieldKey, string[]> = {
  name: ["name", "full name", "full_name", "fullname", "display name", "employee"],
  email: ["email", "email address", "email_address", "work email", "mail"],
  userId: ["user_id", "user id", "userid", "external id", "idp id", "sso id"],
  preferredName: ["preferred_name", "preferred name", "nickname", "known as"],
  photoUrl: ["photo_url", "photo", "photo url", "avatar", "picture", "image"],
  title: ["title", "job title", "job_title", "jobtitle", "role", "position"],
  department: ["department", "dept", "organizational unit", "organisational unit"],
  businessUnit: ["business_unit", "business unit", "bu", "division"],
  employeeType: [
    "employee_type",
    "employee type",
    "worker type",
    "type",
    "employment type",
  ],
  status: ["status", "account status", "active", "state"],
  managerEmail: [
    "manager_email",
    "manager email",
    "manager",
    "reports to",
    "reports_to",
    "reportsto",
    "supervisor",
  ],
  teams: ["teams", "team", "groups", "group", "memberships"],
  orgUnit: ["org_unit", "org unit", "orgunit", "ou"],
  location: ["location", "office", "site"],
  city: ["city", "town"],
  country: ["country", "nation"],
  deskLocation: ["desk_location", "desk location", "desk", "seat", "seat location"],
  startDate: ["start_date", "start date", "hire date", "hire_date", "joined", "joined_date"],
};

const REQUIRED_FIELDS: FieldKey[] = ["name", "email"];

/** Fields written verbatim from a single cell (everything except the special
 *  required/status/teams handling). */
const SCALAR_FIELDS: ScalarField[] = [
  "userId",
  "preferredName",
  "photoUrl",
  "title",
  "department",
  "businessUnit",
  "employeeType",
  "managerEmail",
  "orgUnit",
  "location",
  "city",
  "country",
  "deskLocation",
  "startDate",
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const STATUS_TRUE = new Set(["active", "enabled", "true", "yes", "1"]);
const STATUS_FALSE = new Set(["inactive", "disabled", "false", "no", "0", "suspended"]);

function mapHeaders(headers: string[]): {
  headerToField: Map<string, FieldKey>;
  unknownColumns: string[];
} {
  const headerToField = new Map<string, FieldKey>();
  const unknownColumns: string[] = [];

  for (const header of headers) {
    const lower = header.toLowerCase().trim();
    let matched: FieldKey | null = null;
    for (const [field, aliases] of Object.entries(FIELD_ALIASES)) {
      if (aliases.includes(lower)) {
        matched = field as FieldKey;
        break;
      }
    }
    if (matched) headerToField.set(header, matched);
    else if (header.trim() !== "") unknownColumns.push(header);
  }

  return { headerToField, unknownColumns };
}

function splitTeams(value: string): string[] {
  return value
    .split(/[;,]/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function parseDirectoryCsv(text: string): DirectoryParseResult {
  const { headers, rows } = parseCsv(text);
  const { headerToField, unknownColumns } = mapHeaders(headers);

  const presentFields = new Set(headerToField.values());
  const missingRequiredColumns = REQUIRED_FIELDS.filter(
    (f) => !presentFields.has(f),
  );

  const issues: RowIssue[] = [];
  const people: DirectoryPerson[] = [];

  if (missingRequiredColumns.length > 0) {
    return {
      people: [],
      issues,
      headers,
      missingRequiredColumns,
      unknownColumns,
      totalRows: rows.length,
    };
  }

  const fieldToHeader = new Map<FieldKey, string>();
  headerToField.forEach((field, header) => fieldToHeader.set(field, header));

  const get = (row: Record<string, string>, field: FieldKey) => {
    const header = fieldToHeader.get(field);
    return header ? (row[header] ?? "").trim() : "";
  };

  const seenEmails = new Map<string, number>(); // email -> file row number
  const validEmails = new Set<string>();
  const peopleRows: number[] = [];

  rows.forEach((row, i) => {
    const fileRow = i + 2; // +1 header, +1 for 1-based
    const name = get(row, "name");
    const email = get(row, "email").toLowerCase();

    let rowOk = true;

    if (!name) {
      issues.push({ row: fileRow, field: "name", message: "Name is required.", severity: "error" });
      rowOk = false;
    }

    if (!email) {
      issues.push({ row: fileRow, field: "email", message: "Email is required.", severity: "error" });
      rowOk = false;
    } else if (!EMAIL_RE.test(email)) {
      issues.push({
        row: fileRow,
        field: "email",
        message: `"${email}" is not a valid email address.`,
        severity: "error",
      });
      rowOk = false;
    } else if (seenEmails.has(email)) {
      issues.push({
        row: fileRow,
        field: "email",
        message: `Duplicate email — also on row ${seenEmails.get(email)}.`,
        severity: "error",
      });
      rowOk = false;
    } else {
      seenEmails.set(email, fileRow);
    }

    if (!rowOk) return;

    const managerEmailRaw = get(row, "managerEmail").toLowerCase();
    if (managerEmailRaw && managerEmailRaw === email) {
      issues.push({
        row: fileRow,
        field: "manager_email",
        message: "A person cannot be their own manager.",
        severity: "error",
      });
      return;
    }

    // status: normalize to active/inactive; unknown values warn + default active.
    let status: PersonStatus = "active";
    const statusRaw = get(row, "status").toLowerCase();
    if (statusRaw) {
      if (STATUS_FALSE.has(statusRaw)) status = "inactive";
      else if (STATUS_TRUE.has(statusRaw)) status = "active";
      else {
        issues.push({
          row: fileRow,
          field: "status",
          message: `Unrecognised status "${statusRaw}"; defaulting to active.`,
          severity: "warning",
        });
      }
    }

    // start_date: warn (not block) on an unparseable date.
    const startDate = get(row, "startDate");
    if (startDate && Number.isNaN(Date.parse(startDate))) {
      issues.push({
        row: fileRow,
        field: "start_date",
        message: `"${startDate}" isn't a recognisable date; it will be stored as-is.`,
        severity: "warning",
      });
    }

    const person: DirectoryPerson = {
      name,
      email,
      status,
      managerEmail: managerEmailRaw || undefined,
      teams: splitTeams(get(row, "teams")),
    };
    for (const field of SCALAR_FIELDS) {
      if (field === "managerEmail") continue; // handled above
      const value = get(row, field);
      if (value) person[field] = value;
    }

    validEmails.add(email);
    peopleRows.push(fileRow);
    people.push(person);
  });

  // Manager references must point at someone in the file; missing -> warning.
  people.forEach((person, i) => {
    if (person.managerEmail && !validEmails.has(person.managerEmail)) {
      issues.push({
        row: peopleRows[i],
        field: "manager_email",
        message: `Manager "${person.managerEmail}" is not listed in this file; reporting link will be skipped.`,
        severity: "warning",
      });
    }
  });

  return {
    people,
    issues,
    headers,
    missingRequiredColumns,
    unknownColumns,
    totalRows: rows.length,
  };
}

/** Aggregate counts shown in the preview + final summary. */
export function summarizeDirectory(people: DirectoryPerson[]) {
  const departments = new Set<string>();
  const groups = new Set<string>();
  for (const p of people) {
    if (p.department) departments.add(p.department.toLowerCase());
    for (const t of p.teams) groups.add(t.toLowerCase());
  }
  return {
    people: people.length,
    departments: departments.size,
    groups: groups.size,
  };
}

/** Required + optional column names, for the upload screen's reference chips. */
export const REQUIRED_COLUMNS = ["name", "email"];
export const OPTIONAL_COLUMNS = [
  "title",
  "department",
  "manager_email",
  "teams",
  "business_unit",
  "employee_type",
  "status",
  "org_unit",
  "location",
  "city",
  "country",
  "start_date",
  "user_id",
  "preferred_name",
  "photo_url",
  "desk_location",
];

/** A ready-to-download example CSV covering the most common columns. */
export const DIRECTORY_CSV_TEMPLATE = [
  "name,email,title,department,business_unit,manager_email,teams,status,location,start_date",
  "Ada Lovelace,ada@acme.com,CEO,Executive,Leadership,,Leadership,active,London HQ,2018-01-15",
  "Alan Turing,alan@acme.com,CTO,Engineering,Platform,ada@acme.com,Leadership;Engineering,active,London HQ,2018-03-01",
  "Grace Hopper,grace@acme.com,VP Engineering,Engineering,Platform,alan@acme.com,Engineering,active,Remote,2019-06-10",
  "Katherine Johnson,katherine@acme.com,Data Scientist,Data,Product,grace@acme.com,Data;Research,active,New York,2020-09-21",
].join("\n");
