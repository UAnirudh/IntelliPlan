import type { Ionicons } from "@expo/vector-icons";

type Icon = keyof typeof Ionicons.glyphMap;

/**
 * The website sidebar, in the website's order.
 *
 * Mirrored from Main_Project/templates/base.html (`#sideNavList` plus the
 * Settings/Logout foot) so a student moving between phone and laptop finds
 * the same places in the same order. Keep the two in step: a page added to
 * the sidebar belongs here too.
 *
 * `route` is the native screen when the app has one. Everything else opens
 * the web page already signed in, through the one-time link hand-off —
 * `page` is the key app_link.py allow-lists for it.
 */
export type NavItem = {
  key: string;
  label: string;
  icon: Icon;
  route?: string;
  page?: string;
  blurb: string;
};

export const SIDEBAR: NavItem[] = [
  {
    key: "command_center",
    label: "Command Center",
    icon: "flash-outline",
    page: "command_center",
    blurb: "Chat-first planner on your calendar",
  },
  {
    key: "dashboard",
    label: "Dashboard",
    icon: "grid-outline",
    route: "/tasks",
    blurb: "Everything due, by urgency",
  },
  {
    key: "scheduler",
    label: "Scheduler",
    icon: "calendar-outline",
    route: "/plan",
    blurb: "Your study plan for the week",
  },
  {
    key: "active",
    label: "Active Study",
    icon: "videocam-outline",
    route: "/focus",
    blurb: "A focused, timed session",
  },
  {
    key: "memories",
    label: "Memories",
    icon: "bookmark-outline",
    page: "memories",
    blurb: "Everything you saved, day by day",
  },
  {
    key: "gradebook",
    label: "Grade Modeler",
    icon: "stats-chart-outline",
    route: "/grades",
    blurb: "What you need to hit your target",
  },
  {
    key: "study_hub",
    label: "Study & Learn",
    icon: "school-outline",
    route: "/study",
    blurb: "Turn notes into cards and a quiz",
  },
  {
    key: "flashcards",
    label: "Flashcards",
    icon: "copy-outline",
    page: "flashcards",
    blurb: "Your saved decks",
  },
  {
    key: "streak",
    label: "Streak",
    icon: "flame-outline",
    route: "/streak",
    blurb: "Daily streak, Sparks and the shop",
  },
  {
    key: "pet",
    label: "My Pet",
    icon: "paw-outline",
    page: "pet",
    blurb: "Grows as you study",
  },
  {
    key: "balance",
    label: "Balance",
    icon: "leaf-outline",
    page: "balance",
    blurb: "Your time on IntelliPlan this week",
  },
  {
    key: "features",
    label: "Ideas",
    icon: "bulb-outline",
    page: "features",
    blurb: "Suggest and vote on features",
  },
  {
    key: "my_stats",
    label: "My Stats",
    icon: "pie-chart-outline",
    page: "my_stats",
    blurb: "Your study numbers over time",
  },
];

export const SIDEBAR_FOOT: NavItem[] = [
  {
    key: "settings",
    label: "Settings",
    icon: "settings-outline",
    route: "/settings",
    blurb: "Profile, connections, notifications",
  },
];
