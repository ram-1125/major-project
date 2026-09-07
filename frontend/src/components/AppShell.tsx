import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Bell,
  ChevronLeft,
  ChevronRight,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  X,
} from "lucide-react";

import {
  ALL_NAVIGATION_ITEMS,
  NAVIGATION_GROUP_LABELS,
  NAVIGATION_ITEMS,
  UTILITY_NAVIGATION_ITEMS,
  routeHref,
  type AppRoute,
  type NavigationGroup,
  type NavigationItem,
} from "../navigation";
import { loadSidebarCollapsed, saveSidebarCollapsed } from "../shellPreferences";
import { NavigationSearchDialog } from "./NavigationSearch";
import { ROUTE_ICONS } from "./icons";

type AppShellProps = {
  activeRoute: AppRoute;
  connection: ReactNode;
  refreshAction: ReactNode;
  children: ReactNode;
};

function NavigationLink({ item, activeRoute, collapsed }: {
  item: NavigationItem;
  activeRoute: AppRoute;
  collapsed: boolean;
}) {
  const Icon = ROUTE_ICONS[item.route];
  return (
    <a
      className={`nav-link${activeRoute === item.route ? " nav-link--active" : ""}`}
      href={routeHref(item.route)}
      aria-current={activeRoute === item.route ? "page" : undefined}
      aria-label={item.label}
      title={collapsed ? `${item.label} — ${item.description}` : undefined}
    >
      <span className="nav-link__icon" aria-hidden="true"><Icon size={19} strokeWidth={1.9} /></span>
      <span className="nav-link__content"><span>{item.label}</span></span>
      {item.optional && <span className="nav-link__optional">Optional</span>}
    </a>
  );
}

export function AppShell({ activeRoute, connection, refreshAction, children }: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => loadSidebarCollapsed());
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchSeed, setSearchSeed] = useState("");
  const currentPage = useMemo(
    () => ALL_NAVIGATION_ITEMS.find((item) => item.route === activeRoute) ?? ALL_NAVIGATION_ITEMS[0],
    [activeRoute],
  );

  useEffect(() => { setMenuOpen(false); }, [activeRoute]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        setSearchSeed("");
        setSearchOpen(true);
      }
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  const changeCollapsed = () => {
    setCollapsed((value) => {
      const next = !value;
      saveSidebarCollapsed(next);
      return next;
    });
  };

  const openSearch = (seed = "") => {
    setSearchSeed(seed);
    setSearchOpen(true);
  };

  const renderGroup = (group: NavigationGroup, items: NavigationItem[]) => (
    <div className="sidebar__group" key={group}>
      <p className="sidebar__label">{NAVIGATION_GROUP_LABELS[group]}</p>
      {items.map((item) => <NavigationLink key={item.route} item={item} activeRoute={activeRoute} collapsed={collapsed} />)}
    </div>
  );

  return (
    <div className={`app-shell${collapsed ? " app-shell--collapsed" : ""}`} data-fixed-layout="true">
      <a className="skip-link" href="#main-content">Skip to main content</a>

      <header className="topbar" data-fixed-ribbon="true">
        <div className="topbar__identity">
          <button
            className="menu-toggle icon-button icon-button--on-dark"
            type="button"
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-controls="primary-navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >
            {menuOpen ? <X aria-hidden="true" size={21} /> : <Menu aria-hidden="true" size={21} />}
          </button>
          <a className="mobile-brand" href={routeHref("overview")} aria-label="SmartOps overview">
            <span className="brand__mark" aria-hidden="true">S</span>
          </a>
          <div className="topbar__page">
            <strong>{currentPage.label}</strong>
            <span>{currentPage.description}</span>
          </div>
        </div>
        <div className="header-actions">
          <button className="topbar-search" type="button" onClick={() => openSearch()} aria-label="Search SmartOps navigation">
            <Search aria-hidden="true" size={18} /><span>Search</span><kbd>Ctrl K</kbd>
          </button>
          <div className="topbar__status">{connection}</div>
          {refreshAction}
          <a className="icon-button icon-button--on-dark topbar__shortcut" href="#/settings?section=notifications" aria-label="Open notification settings" title="Notification settings">
            <Bell aria-hidden="true" size={19} />
          </a>
          <a className="icon-button icon-button--on-dark topbar__shortcut" href={routeHref("settings")} aria-label="Open Settings" title="Settings">
            <Settings aria-hidden="true" size={19} />
          </a>
        </div>
      </header>

      <div className="app-shell__body">
        <aside
          id="primary-navigation"
          className={`sidebar${menuOpen ? " sidebar--open" : ""}`}
          aria-label="SmartOps sections"
          data-fixed-navigation="true"
          data-collapsed={collapsed}
        >
          <div className="sidebar__top">
            <a className="brand" href={routeHref("overview")} aria-label="SmartOps overview">
              <span className="brand__mark" aria-hidden="true">S</span>
              <span className="brand__text"><strong>SmartOps</strong><small>Local predictive PC monitoring</small></span>
            </a>
            <div className="sidebar-search-field">
              <Search aria-hidden="true" size={17} />
              <label className="visually-hidden" htmlFor="sidebar-navigation-search">Search Navigation</label>
              <input
                id="sidebar-navigation-search"
                value=""
                placeholder="Search Navigation"
                readOnly
                onFocus={() => openSearch()}
                onClick={() => openSearch()}
                aria-keyshortcuts="Control+K Meta+K"
              />
              <kbd>Ctrl K</kbd>
            </div>
            <button className="sidebar-search-compact icon-button icon-button--on-dark" type="button" aria-label="Search Navigation" title="Search Navigation (Ctrl+K)" onClick={() => openSearch()}>
              <Search aria-hidden="true" size={19} />
            </button>
          </div>

          <nav aria-label="Primary navigation">
            {renderGroup("monitoring", NAVIGATION_ITEMS.filter((item) => item.group === "monitoring"))}
            {renderGroup("insights", NAVIGATION_ITEMS.filter((item) => item.group === "insights"))}
          </nav>

          <div className="sidebar__bottom">
            <nav className="utility-navigation" aria-label="Application utilities">
              {renderGroup("application", UTILITY_NAVIGATION_ITEMS)}
            </nav>
            <div className="sidebar__privacy">
              <ShieldCheck aria-hidden="true" size={18} />
              <span><strong>Local-only operation</strong><small>No system data leaves this PC.</small></span>
            </div>
            <button className="sidebar-collapse" type="button" onClick={changeCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>
              {collapsed ? <ChevronRight aria-hidden="true" size={18} /> : <ChevronLeft aria-hidden="true" size={18} />}
              <span>{collapsed ? "Expand" : "Collapse sidebar"}</span>
            </button>
          </div>
        </aside>
        {menuOpen && <button type="button" className="sidebar-backdrop" aria-label="Close navigation menu" onClick={() => setMenuOpen(false)} />}
        <main id="main-content" className="app-content" data-scroll-owner="true" tabIndex={-1}>{children}</main>
      </div>
      <NavigationSearchDialog open={searchOpen} initialQuery={searchSeed} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
