import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Search, X } from "lucide-react";

import {
  searchNavigation,
  type NavigationSearchItem,
  type SearchGroup,
} from "../searchCatalogue";

const GROUPS: SearchGroup[] = ["Pages", "Settings", "PC Quality Profiles"];

function HighlightedText({ text, query }: { text: string; query: string }) {
  const needle = query.trim();
  if (!needle) return <>{text}</>;
  const index = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  if (index < 0) return <>{text}</>;
  return <>{text.slice(0, index)}<mark>{text.slice(index, index + needle.length)}</mark>{text.slice(index + needle.length)}</>;
}

function grouped(items: NavigationSearchItem[]) {
  return GROUPS.map((group) => ({ group, items: items.filter((item) => item.group === group) }))
    .filter((entry) => entry.items.length > 0);
}

export function NavigationSearchDialog({
  open,
  initialQuery,
  onClose,
}: {
  open: boolean;
  initialQuery: string;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const results = useMemo(() => searchNavigation(query), [query]);
  const groupedResults = useMemo(() => grouped(results), [results]);

  useEffect(() => {
    if (!open) return;
    setQuery(initialQuery);
    setSelectedIndex(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [initialQuery, open]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  if (!open) return null;

  const choose = (item: NavigationSearchItem) => {
    onClose();
    window.location.hash = item.href.slice(1);
  };

  return (
    <div className="navigation-search-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        className="navigation-search-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Search SmartOps navigation"
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); onClose(); }
          if (event.key === "ArrowDown" && results.length) { event.preventDefault(); setSelectedIndex((value) => (value + 1) % results.length); }
          if (event.key === "ArrowUp" && results.length) { event.preventDefault(); setSelectedIndex((value) => (value - 1 + results.length) % results.length); }
          if (event.key === "Enter" && results[selectedIndex]) { event.preventDefault(); choose(results[selectedIndex]); }
        }}
      >
        <div className="navigation-search-dialog__header">
          <Search aria-hidden="true" size={20} />
          <label className="visually-hidden" htmlFor="global-navigation-search">Search SmartOps navigation</label>
          <input
            id="global-navigation-search"
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search pages, settings and PC Quality profiles"
            autoComplete="off"
          />
          <kbd>Esc</kbd>
          <button type="button" className="icon-button" aria-label="Close navigation search" onClick={onClose}>
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        <div className="navigation-search-results" role="listbox" aria-label="Navigation search results">
          {results.length === 0 ? (
            <div className="navigation-search-empty">
              <Search aria-hidden="true" size={24} />
              <strong>No results</strong>
              <span>Try a page, setting, feature, or workload profile.</span>
            </div>
          ) : groupedResults.map((entry) => (
            <section key={entry.group} className="navigation-search-group" aria-label={entry.group}>
              <h2>{entry.group}</h2>
              {entry.items.map((item) => {
                const flatIndex = results.findIndex((result) => result.id === item.id);
                return (
                  <a
                    key={item.id}
                    href={item.href}
                    role="option"
                    aria-selected={flatIndex === selectedIndex}
                    className={flatIndex === selectedIndex ? "navigation-search-result navigation-search-result--selected" : "navigation-search-result"}
                    onMouseEnter={() => setSelectedIndex(flatIndex)}
                    onClick={onClose}
                  >
                    <span><strong><HighlightedText text={item.label} query={query} /></strong><small>{item.description}</small></span>
                    <ArrowRight aria-hidden="true" size={17} />
                  </a>
                );
              })}
            </section>
          ))}
        </div>
        <footer className="navigation-search-dialog__footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span>
          <span>Local application navigation only</span>
        </footer>
      </section>
    </div>
  );
}
