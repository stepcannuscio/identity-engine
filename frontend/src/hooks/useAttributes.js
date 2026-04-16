import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAttributes, getDomains } from '../api/endpoints.js'

export function useAttributes() {
  const queryClient = useQueryClient()

  const domainsQuery = useQuery({
    queryKey: ['domains'],
    queryFn: getDomains,
  })

  const attributesQuery = useQuery({
    queryKey: ['attributes'],
    queryFn: () => getAttributes(),
  })

  const groupedAttributes = useMemo(() => {
    return (attributesQuery.data ?? []).reduce((groups, attribute) => {
      const bucket = groups[attribute.domain] ?? []
      bucket.push(attribute)
      groups[attribute.domain] = bucket
      return groups
    }, {})
  }, [attributesQuery.data])

  const refreshAttributes = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['attributes'] }),
      queryClient.invalidateQueries({ queryKey: ['domains'] }),
    ])
  }, [queryClient])

  return {
    domains: domainsQuery.data ?? [],
    attributes: attributesQuery.data ?? [],
    groupedAttributes,
    isLoading: domainsQuery.isLoading || attributesQuery.isLoading,
    isError: domainsQuery.isError || attributesQuery.isError,
    refreshAttributes,
  }
}
