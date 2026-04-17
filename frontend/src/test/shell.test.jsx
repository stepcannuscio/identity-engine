import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLocation } from 'react-router-dom'
import Header from '../components/layout/Header.jsx'
import TabBar from '../components/layout/TabBar.jsx'
import { renderWithProviders } from './renderWithProviders.jsx'

function LocationDisplay() {
  const location = useLocation()
  return <div>{location.pathname}</div>
}

describe('Header', () => {
  it('toggles between local and external backends', async () => {
    const user = userEvent.setup()

    renderWithProviders(<Header />)

    const button = screen.getByRole('button', { name: 'local' })
    expect(button).toHaveAttribute('title', 'Queries processed on your device')

    await user.click(button)

    expect(screen.getByRole('button', { name: 'external' })).toHaveAttribute(
      'title',
      'Queries sent to Claude API (ZDR enabled)',
    )
  })
})

describe('TabBar', () => {
  it('prevents navigation while logged out', async () => {
    const user = userEvent.setup()

    renderWithProviders(
      <>
        <TabBar isAuthenticated={false} />
        <LocationDisplay />
      </>,
      { route: '/query' },
    )

    const historyLink = screen.getByRole('link', { name: 'History' })
    expect(historyLink).toHaveClass('disabled')

    await user.click(historyLink)

    expect(screen.getByText('/query')).toBeInTheDocument()
    expect(historyLink).not.toHaveClass('active')
  })

  it('marks the active route when authenticated', async () => {
    const user = userEvent.setup()

    renderWithProviders(
      <>
        <TabBar isAuthenticated />
        <LocationDisplay />
      </>,
      { route: '/query' },
    )

    const graphLink = screen.getByRole('link', { name: 'Identity Graph' })

    await user.click(graphLink)

    expect(screen.getByText('/graph')).toBeInTheDocument()
    expect(graphLink).toHaveClass('active')
  })
})
