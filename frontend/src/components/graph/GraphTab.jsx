import { useMemo, useState } from 'react'
import DomainSection from './DomainSection.jsx'
import AttributeEditor from './AttributeEditor.jsx'
import CapturePanel from './CapturePanel.jsx'
import { useAttributes } from '../../hooks/useAttributes.js'

export default function GraphTab() {
  const {
    confirmAttribute,
    domains,
    rejectAttribute,
    attributes,
    isLoading,
    isError,
    refreshAttributes,
  } = useAttributes()
  const [selectedDomain, setSelectedDomain] = useState('')
  const [search, setSearch] = useState('')
  const [expandedDomains, setExpandedDomains] = useState({})
  const [editingAttribute, setEditingAttribute] = useState(null)

  const filteredAttributes = useMemo(() => {
    const query = search.trim().toLowerCase()
    return attributes.filter((attribute) => {
      const matchesDomain = !selectedDomain || attribute.domain === selectedDomain
      if (!matchesDomain) {
        return false
      }

      if (!query) {
        return true
      }

      return [
        attribute.domain,
        attribute.label,
        attribute.value,
        attribute.elaboration,
      ]
        .filter(Boolean)
        .some((field) => field.toLowerCase().includes(query))
    })
  }, [attributes, search, selectedDomain])

  const groupedAttributes = useMemo(() => {
    return filteredAttributes.reduce((groups, attribute) => {
      const bucket = groups[attribute.domain] ?? []
      bucket.push(attribute)
      groups[attribute.domain] = bucket
      return groups
    }, {})
  }, [filteredAttributes])

  const visibleDomains = selectedDomain
    ? domains.filter((domain) => domain.domain === selectedDomain)
    : domains

  const handleSaved = async () => {
    await refreshAttributes()
    setEditingAttribute(null)
  }

  const handleConfirm = async (attribute) => {
    await confirmAttribute(attribute.id)
  }

  const handleReject = async (attribute) => {
    await rejectAttribute(attribute.id)
  }

  return (
    <section className="graph-tab">
      <div className="graph-layout">
        <aside className="graph-sidebar">
          <div className="sidebar-header">
            <div>
              <p className="eyebrow">Identity graph</p>
              <h2 className="sidebar-title">Attributes</h2>
            </div>
          </div>
          <input
            type="search"
            placeholder="Search attributes"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <button
            type="button"
            className="button-secondary"
            onClick={() =>
              setEditingAttribute({
                domain: selectedDomain || domains[0]?.domain || 'personality',
                label: '',
                value: '',
                elaboration: '',
                confidence: 0.5,
                mutability: 'evolving',
                routing: 'local_only',
              })
            }
          >
            Add attribute
          </button>
          <div className="sidebar-domain-list">
            <button
              type="button"
              className={`sidebar-domain ${selectedDomain === '' ? 'active' : ''}`}
              onClick={() => setSelectedDomain('')}
            >
              <span>All attributes</span>
              <span className="domain-count">{attributes.length}</span>
            </button>
            {domains.map((domain) => (
              <button
                key={domain.domain}
                type="button"
                className={`sidebar-domain ${
                  selectedDomain === domain.domain ? 'active' : ''
                }`}
                onClick={() => setSelectedDomain(domain.domain)}
              >
                <span>{domain.domain}</span>
                <span className="domain-count">{domain.attribute_count}</span>
              </button>
            ))}
          </div>
        </aside>

        <div className="graph-main">
          <div className="mobile-domain-select">
            <select
              value={selectedDomain}
              onChange={(event) => setSelectedDomain(event.target.value)}
            >
              <option value="">All domains</option>
              {domains.map((domain) => (
                <option key={domain.domain} value={domain.domain}>
                  {domain.domain}
                </option>
              ))}
            </select>
          </div>

          <CapturePanel domains={domains} onSaved={handleSaved} />

          {isLoading ? <div className="empty-state">Loading attributes...</div> : null}
          {isError ? (
            <div className="empty-state">Unable to load the identity graph.</div>
          ) : null}
          {!isLoading && !isError && filteredAttributes.length === 0 ? (
            <div className="empty-state">No attributes match this view yet.</div>
          ) : null}

          {!isLoading && !isError
            ? visibleDomains
                .filter((domain) => groupedAttributes[domain.domain]?.length)
                .map((domain) => (
                  <DomainSection
                    key={domain.domain}
                    domain={domain.domain}
                    attributes={groupedAttributes[domain.domain]}
                    onConfirm={handleConfirm}
                    onReject={handleReject}
                    expanded={
                      search
                        ? true
                        : domain.domain === selectedDomain ||
                          Boolean(expandedDomains[domain.domain])
                    }
                    onToggle={() =>
                      setExpandedDomains((current) => ({
                        ...current,
                        [domain.domain]: !current[domain.domain],
                      }))
                    }
                    onEdit={setEditingAttribute}
                  />
                ))
            : null}
        </div>
      </div>

      <AttributeEditor
        attribute={editingAttribute}
        domains={domains}
        isOpen={Boolean(editingAttribute)}
        onClose={() => setEditingAttribute(null)}
        onSaved={handleSaved}
      />
    </section>
  )
}
