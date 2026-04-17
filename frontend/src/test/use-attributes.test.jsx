import { act, renderHook, waitFor } from '@testing-library/react'
import { vi } from 'vitest'
import { useAttributes } from '../hooks/useAttributes.js'
import { createAttribute, createDomain } from './fixtures.js'
import { createTestQueryClient, createWrapper } from './renderWithProviders.jsx'
import { getAttributes, getDomains } from '../api/endpoints.js'

vi.mock('../api/endpoints.js', () => ({
  getAttributes: vi.fn(),
  getDomains: vi.fn(),
}))

describe('useAttributes', () => {
  it('groups attributes by domain and returns the loaded records', async () => {
    getDomains.mockResolvedValue([
      createDomain({ domain: 'personality' }),
      createDomain({ domain: 'values' }),
    ])
    getAttributes.mockResolvedValue([
      createAttribute({ id: 'a1', domain: 'personality' }),
      createAttribute({ id: 'a2', domain: 'values', label: 'honesty' }),
    ])

    const { result } = renderHook(() => useAttributes(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.domains).toHaveLength(2)
    expect(result.current.attributes).toHaveLength(2)
    expect(result.current.groupedAttributes.personality).toHaveLength(1)
    expect(result.current.groupedAttributes.values).toHaveLength(1)
  })

  it('reports loading while queries are in flight', () => {
    getDomains.mockReturnValue(new Promise(() => {}))
    getAttributes.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useAttributes(), {
      wrapper: createWrapper(),
    })

    expect(result.current.isLoading).toBe(true)
  })

  it('reports errors when either query fails', async () => {
    getDomains.mockRejectedValue(new Error('failed'))
    getAttributes.mockResolvedValue([])

    const { result } = renderHook(() => useAttributes(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })
  })

  it('invalidates domains and attributes when refreshed', async () => {
    const queryClient = createTestQueryClient()
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
    getDomains.mockResolvedValue([createDomain()])
    getAttributes.mockResolvedValue([createAttribute()])

    const { result } = renderHook(() => useAttributes(), {
      wrapper: createWrapper({ queryClient }),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    await act(async () => {
      await result.current.refreshAttributes()
    })

    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['attributes'] })
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['domains'] })
  })
})
