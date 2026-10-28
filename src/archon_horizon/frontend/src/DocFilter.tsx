import { useSearchParams } from 'react-router-dom';
import { documentsIn } from './homalg';
export default function DocFilter({ state }: { state: any }) {
  const [params, setParams] = useSearchParams();
  const value = params.get('doc') || '';
  const documents = documentsIn(state);
  if (value && !documents.includes(value)) documents.push(value);
  return (
    <label className="homalg-doc-filter">
      Document{' '}
      <select
        aria-label="Document"
        value={value}
        onChange={(event) => {
          const next = new URLSearchParams(params);
          if (event.target.value) next.set('doc', event.target.value);
          else next.delete('doc');
          next.delete('focus');
          setParams(next);
        }}
      >
        <option value="">All documents</option>
        {documents.map((doc) => (
          <option key={doc}>{doc}</option>
        ))}
      </select>
    </label>
  );
}
