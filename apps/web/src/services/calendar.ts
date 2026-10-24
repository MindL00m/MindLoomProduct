/** Google Calendar scheduling for meetings LoomBot drafts in Ask and workspaces. */

import { apiFetch } from "@/lib/api";

export interface CalendarStatus {
  connected: boolean;
  can_schedule: boolean;
  account?: string | null;
  reason?: string | null;
}

export interface MeetingSuggestion {
  start: string;
  end: string;
  label: string;
}

export interface Availability {
  timezone: string;
  duration_minutes: number;
  suggestions: MeetingSuggestion[];
  availability_checked_for: string[];
  unknown_availability: { email: string; reason: string }[];
  note?: string;
  error?: string;
}

export interface ScheduledMeeting {
  status: "scheduled";
  event_id: string;
  html_link?: string | null;
  meet_link?: string | null;
  title: string;
  start: string;
  end: string;
  attendees: string[];
  label: string;
}

async function post<T>(path: string, body: unknown, fallback: string): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof payload.detail === "string" ? payload.detail : fallback);
  }
  return response.json() as Promise<T>;
}

export async function getCalendarStatus(): Promise<CalendarStatus> {
  const response = await apiFetch("/calendar/status");
  if (!response.ok) throw new Error("Could not check Google Calendar.");
  return response.json();
}

/** Free times shared by you and the attendees whose calendars are readable. */
export function findMeetingTimes(input: {
  attendee_emails: string[];
  duration_minutes: number;
  start_date?: string;
  end_date?: string;
  timezone?: string;
}): Promise<Availability> {
  return post("/calendar/availability", input, "Could not check availability.");
}

/** Create the meeting on your Google Calendar; Google emails the invitations. */
export function createMeeting(input: {
  title: string;
  start: string;
  end: string;
  attendees: string[];
  description?: string;
  location?: string;
  add_video_call: boolean;
  timezone?: string;
}): Promise<ScheduledMeeting> {
  return post("/calendar/meetings", input, "Could not schedule the meeting.");
}
