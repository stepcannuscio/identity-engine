import clsx from 'clsx'
import { NavLink } from 'react-router-dom'

const TABS = [
  { label: 'Query', to: '/query' },
  { label: 'Identity Graph', to: '/graph' },
  { label: 'History', to: '/history' },
]

export default function TabBar({ isAuthenticated }) {
  return (
    <nav className="app-tabbar" aria-label="Primary">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            clsx('tab-link', {
              active: isActive && isAuthenticated,
              disabled: !isAuthenticated,
            })
          }
          onClick={(event) => {
            if (!isAuthenticated) {
              event.preventDefault()
            }
          }}
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  )
}
