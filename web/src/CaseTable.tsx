import { useState } from "react";
import type { MouseEvent } from "react";
import { ChangeBadge } from "./CaseDetail";
import type { EvaluationCase, Trial } from "./data";
import { Icon } from "./Icons";
import { caseChange, labelize, summarizeTrials } from "./presentation";

function matchingStatus(evaluationCase: EvaluationCase, status: string) {
  switch (status) {
    case "regressed": return caseChange(evaluationCase) === "Regressed";
    case "improved": return caseChange(evaluationCase) === "Improved";
    case "failed": return evaluationCase.candidate.some((trial) => trial.status !== "error" && !trial.passed);
    case "error": return [...evaluationCase.baseline, ...evaluationCase.candidate].some((trial) => trial.status === "error");
    default: return true;
  }
}

function PassRate({ trials }: { trials: Trial[] }) {
  if (!trials.length) return <span aria-label="No trials" className="muted">—</span>;
  const { passed, scored, errors } = summarizeTrials(trials);
  return (
    <span className="pass-rate">
      {scored > 0
        ? <span aria-label={`${passed} of ${scored} scored trials passed`}>{passed}<span className="rate-slash"> / </span>{scored}</span>
        : <span className="muted" aria-label="No scored trials">—</span>}
      {errors > 0 && <small>{errors} {errors === 1 ? "error" : "errors"}</small>}
    </span>
  );
}

interface CaseTableProps {
  cases: EvaluationCase[];
  selected: string | undefined;
  getLink: (caseId: string) => string;
  openCase: (event: MouseEvent<HTMLAnchorElement>) => void;
}

export function CaseTable({ cases, selected, getLink, openCase }: CaseTableProps) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("any");
  const [category, setCategory] = useState("");
  const search = query.trim().toLowerCase();
  const categories = [...new Set(cases.map((item) => item.category))].sort((first, second) => first.localeCompare(second));
  const matches = cases.filter((item) => {
    const content = `${item.id} ${item.question} ${item.category} ${labelize(item.category)} ${item.cluster} ${labelize(item.cluster)}`.toLowerCase();
    return (!category || item.category === category) && (!search || content.includes(search)) && matchingStatus(item, status);
  });

  function resetFilters() {
    setQuery("");
    setStatus("any");
    setCategory("");
  }

  return (
    <section className="panel case-list" aria-labelledby="cases-heading">
      <header className="section-header">
        <div className="section-titles">
          <h2 id="cases-heading">Case results</h2>
          <p className="muted count-text" role="status">{matches.length} of {cases.length} {cases.length === 1 ? "case" : "cases"}</p>
        </div>
        <div className="section-actions">
          {(query || status !== "any" || category) && <button type="button" className="quiet-button" onClick={resetFilters}>Clear filters</button>}
          {selected && <a className="quiet-button inspect-link" href={getLink(selected)} onClick={openCase}>Inspect {selected}</a>}
        </div>
      </header>
      {cases.length > 0 && (
        <div className="filters">
          <label className="search-field">
            <Icon name="search" />
            <span className="sr-only">Search cases</span>
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search cases" />
          </label>
          <label className="select-field">
            <span className="sr-only">Filter by category</span>
            <select value={category} onChange={(event) => setCategory(event.target.value)}>
              <option value="">All categories</option>
              {categories.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}
            </select>
            <Icon name="chevron" />
          </label>
          <label className="select-field">
            <span className="sr-only">Filter by result</span>
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="any">All results</option>
              <option value="regressed">Regressed</option>
              <option value="improved">Improved</option>
              <option value="failed">Candidate failed</option>
              <option value="error">Trial errors</option>
            </select>
            <Icon name="chevron" />
          </label>
        </div>
      )}
      {matches.length ? (
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Case results table">
          <table>
            <thead><tr><th scope="col">Evaluation case</th><th scope="col">Baseline passed</th><th scope="col">Candidate passed</th><th scope="col">Change</th></tr></thead>
            <tbody>
              {matches.map((item) => (
                <tr key={item.id} className={item.id === selected ? "selected" : undefined}>
                  <th scope="row">
                    <a className="case-link" href={getLink(item.id)} onClick={openCase} aria-current={item.id === selected ? "true" : undefined}>
                      <span className="case-meta"><span className="case-id">{item.id}</span><span>{labelize(item.category)}</span></span>
                      <span className="case-name">{item.question}</span>
                    </a>
                  </th>
                  <td><PassRate trials={item.baseline} /></td>
                  <td><PassRate trials={item.candidate} /></td>
                  <td><ChangeBadge evaluationCase={item} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="list-empty muted">{cases.length ? "No cases match these filters." : "This comparison has no recorded cases."}</p>}
    </section>
  );
}
