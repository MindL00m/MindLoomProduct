import { AnimatePresence, motion } from "framer-motion";
import {
  Briefcase,
  Building2,
  CalendarDays,
  Mail,
  MapPin,
  Users,
  X,
} from "lucide-react";
import type { OrgChartPerson } from "@/lib/orgChart";
import { avatarColor, initials } from "@/lib/orgChart";
import { StatusBadge } from "./StatusBadge";
import { cn } from "@/lib/utils";

export interface EmployeeProfileProps {
  person: OrgChartPerson | null;
  /** Resolved manager (for the "Reports to" row), if any. */
  manager?: OrgChartPerson | null;
  /** Direct reports, for the count + quick links. */
  reports?: OrgChartPerson[];
  onClose: () => void;
  onSelectPerson?: (id: string) => void;
}

/** Slide-in panel showing a person's public-facing directory profile. */
export function EmployeeProfile({
  person,
  manager,
  reports = [],
  onClose,
  onSelectPerson,
}: EmployeeProfileProps) {
  return (
    <AnimatePresence>
      {person && (
        <motion.aside
          key={person.id}
          initial={{ x: 24, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 24, opacity: 0 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className="flex w-full shrink-0 flex-col overflow-y-auto border-l border-border bg-card sm:w-80"
          aria-label={`Profile: ${person.name}`}
        >
          <div className="flex items-center justify-between p-4">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Employee profile
            </span>
            <button
              onClick={onClose}
              aria-label="Close profile"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="size-4" />
            </button>
          </div>

          <div className="flex flex-col items-center px-6 pb-2 text-center">
            <Avatar person={person} size={72} />
            <h2 className="mt-3 text-lg font-semibold tracking-tight">
              {person.preferredName || person.name}
            </h2>
            {person.title && (
              <p className="text-sm text-muted-foreground">{person.title}</p>
            )}
            {person.status && (
              <StatusBadge
                tone={person.status === "active" ? "healthy" : "neutral"}
                dot
                className="mt-2 capitalize"
              >
                {person.status}
              </StatusBadge>
            )}
          </div>

          <dl className="space-y-1 p-4">
            <Detail icon={<Mail />} label="Email">
              {person.email ? (
                <a
                  href={`mailto:${person.email}`}
                  className="text-primary hover:underline"
                >
                  {person.email}
                </a>
              ) : null}
            </Detail>
            <Detail icon={<Briefcase />} label="Department">
              {person.department}
            </Detail>
            <Detail icon={<Building2 />} label="Business unit">
              {person.businessUnit}
            </Detail>
            <Detail icon={<MapPin />} label="Location">
              {[person.location, person.city, person.country]
                .filter(Boolean)
                .join(", ") || null}
            </Detail>
            <Detail icon={<CalendarDays />} label="Start date">
              {person.startDate}
            </Detail>
            <Detail icon={<Briefcase />} label="Reports to">
              {manager ? (
                <button
                  onClick={() => onSelectPerson?.(manager.id)}
                  className="text-primary hover:underline"
                >
                  {manager.name}
                </button>
              ) : (
                "—"
              )}
            </Detail>
          </dl>

          {person.groups.length > 0 && (
            <div className="px-4 pb-4">
              <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Users className="size-3.5" /> Teams
              </p>
              <div className="flex flex-wrap gap-1.5">
                {person.groups.map((g) => (
                  <span
                    key={g}
                    className="rounded-full bg-secondary px-2.5 py-0.5 text-xs text-secondary-foreground ring-1 ring-inset ring-border"
                  >
                    {g}
                  </span>
                ))}
              </div>
            </div>
          )}

          {reports.length > 0 && (
            <div className="px-4 pb-6">
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">
                Direct reports ({reports.length})
              </p>
              <ul className="space-y-1">
                {reports.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => onSelectPerson?.(r.id)}
                      className="flex w-full items-center gap-2 rounded-md p-1.5 text-left text-sm transition-colors hover:bg-secondary"
                    >
                      <Avatar person={r} size={24} />
                      <span className="min-w-0 flex-1 truncate">{r.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

function Detail({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children?: React.ReactNode;
}) {
  if (!children) return null;
  return (
    <div className="flex items-start gap-3 px-1 py-2">
      <span className="mt-0.5 text-mist-700 [&_svg]:size-4" aria-hidden="true">
        {icon}
      </span>
      <div className="min-w-0">
        <dt className="text-xs text-muted-foreground">{label}</dt>
        <dd className="text-sm">{children}</dd>
      </div>
    </div>
  );
}

export function Avatar({
  person,
  size = 40,
}: {
  person: OrgChartPerson;
  size?: number;
}) {
  const dim = { width: size, height: size };
  if (person.photoUrl) {
    return (
      <img
        src={person.photoUrl}
        alt=""
        style={dim}
        className="rounded-full object-cover ring-2 ring-background"
      />
    );
  }
  return (
    <span
      style={{ ...dim, backgroundColor: avatarColor(person.id) }}
      className={cn(
        "flex items-center justify-center rounded-full font-semibold text-white ring-2 ring-background",
      )}
    >
      <span style={{ fontSize: size * 0.36 }}>{initials(person.name)}</span>
    </span>
  );
}
