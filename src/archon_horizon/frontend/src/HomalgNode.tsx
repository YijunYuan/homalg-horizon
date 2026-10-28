import { Link, useSearchParams } from 'react-router-dom';
import { nodeAppearance, overlayOf, sourcePages } from './homalg';
export function SourceLinks({ sources }: { sources: unknown }) {
  const [params] = useSearchParams();
  const doc = params.get('doc');
  const pages = sourcePages(sources);
  return pages.length ? (
    <span className="homalg-source-links">
      Source pages:{' '}
      {pages.map(({ slug, page }) => (
        <Link
          key={`${slug}:${page}`}
          to={`/reference?slug=${encodeURIComponent(slug)}&page=${page}${doc ? `&doc=${encodeURIComponent(doc)}` : ''}`}
        >
          {slug}:page-{page}
        </Link>
      ))}
    </span>
  ) : null;
}
export function HomalgBadges({ node }: { node: any }) {
  const status = overlayOf(node),
    appearance = nodeAppearance(node);
  if (!status || !appearance) return null;
  const fidelity =
    status.fidelity_status ||
    status.fidelity?.status ||
    (typeof status.fidelity === 'string' ? status.fidelity : '');
  return (
    <span className="homalg-badges">
      <span
        className="homalg-status"
        style={{
          background: appearance.fill,
          color: appearance.color,
          borderColor: appearance.border,
          borderStyle: appearance.dashed ? 'dashed' : 'solid',
        }}
      >
        {appearance.glyph} {appearance.label}
      </span>
      <span className="badge">
        {String(status.status || '').replace(/-/g, ' ')}
      </span>
      {fidelity && <span className="badge">{fidelity}</span>}
      {status.is_aux && (
        <span className="badge">
          {status.aux_kind === 'bridge' ? 'bridge' : 'auxiliary'}
        </span>
      )}
      {(status.cited ||
        String(node.id).split('.')[1]?.startsWith('Cited_')) && (
        <span className="badge">cited</span>
      )}
      {status.status === 'proved-modulo-citations' && (
        <span className="badge">relies on cited results</span>
      )}
      {status.status === 'proved-with-axioms' && (
        <span className="badge homalg-warning">nonstandard axioms</span>
      )}
      {(status.dispute_raised || status.disputed) &&
        status.headline !== 'error' && (
          <span className="badge">dispute raised</span>
        )}
      {status.needs_repair && (
        <span className="badge homalg-warning">⚒ needs repair</span>
      )}
      {status.reopened && (
        <span className="badge homalg-warning">reopened</span>
      )}
      {status.deps_mismatch && (
        <span className="badge homalg-warning">dependencies mismatch</span>
      )}
      {status.approx && (
        <span
          className="badge homalg-warning"
          title="Derived data is absent or stale; this is a text-based estimate"
        >
          approximate
        </span>
      )}
      {status.reviewed === false && <span className="badge">unreviewed</span>}
    </span>
  );
}
