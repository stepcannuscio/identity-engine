import { ChevronDown, ChevronRight } from 'lucide-react'
import AttributeCard from './AttributeCard.jsx'

function titleCase(value) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export default function DomainSection({
  domain,
  attributes,
  onConfirm,
  onReject,
  expanded,
  onToggle,
  onEdit,
}) {
  return (
    <section className="domain-section">
      <button type="button" className="section-toggle" onClick={onToggle}>
        <div className="section-title-group">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <h3 className="section-title">{titleCase(domain)}</h3>
          <span className="domain-badge">{attributes.length}</span>
        </div>
      </button>
      {expanded ? (
        <div className="section-body">
          {attributes.map((attribute) => (
            <AttributeCard
              key={attribute.id}
              attribute={attribute}
              onConfirm={() => onConfirm(attribute)}
              onEdit={() => onEdit(attribute)}
              onReject={() => onReject(attribute)}
            />
          ))}
        </div>
      ) : null}
    </section>
  )
}
