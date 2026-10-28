import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getReferencePage, type ReferencePageData } from './api';
export default function ReferencePage() {
  const [params] = useSearchParams(),
    slug = params.get('slug') || '',
    page = params.get('page') || '';
  const [data, setData] = useState<ReferencePageData>(),
    [error, setError] = useState(''),
    [imageError, setImageError] = useState(false);
  useEffect(() => {
    let active = true;
    setData(undefined);
    setError('');
    setImageError(false);
    getReferencePage(slug, page)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((reason) => {
        if (active) setError(String(reason));
      });
    return () => {
      active = false;
    };
  }, [slug, page]);
  return (
    <section className="page homalg-reference">
      <h2>
        {slug}: page {page}
      </h2>
      <p>Private source transcription · PDF page index</p>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p role="status">Loading source page…</p>}
      {data && (
        <>
          <p>
            <span className="homalg-status">{data.status.status}</span>
            {data.stale && (
              <span className="homalg-warning">
                {' '}
                Changed since review; a new review is required.
              </span>
            )}
          </p>
          <div className="homalg-reference-grid">
            <div>
              <h3>Page image</h3>
              {imageError ? (
                <p>Page image is unavailable. Render the source PDF locally.</p>
              ) : (
                <a
                  href={`/api/reference-page.png?slug=${encodeURIComponent(slug)}&page=${encodeURIComponent(page)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    alt={`Source scan of ${slug}, PDF page ${page}`}
                    src={`/api/reference-page.png?slug=${encodeURIComponent(slug)}&page=${encodeURIComponent(page)}`}
                    onError={() => setImageError(true)}
                  />
                </a>
              )}
            </div>
            <div>
              <h3>Vision transcription</h3>
              <pre className="code-block">{data.text}</pre>
            </div>
          </div>
          <h3>Review history</h3>
          {!data.status.reviews?.length && <p>No reviews recorded.</p>}
          <ul>
            {data.status.reviews?.map((review, index) => (
              <li key={index}>
                <strong>{review.status}</strong> · {review.by} · {review.at}
                {review.findings?.map((finding, i) => (
                  <p key={i}>
                    {finding.severity}: {finding.kind} — {finding.where}
                    {finding.note ? `: ${finding.note}` : ''}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
