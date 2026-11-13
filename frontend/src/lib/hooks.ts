import { useEffect, useState } from "react"

/** Atrasa a atualização de um valor (ex.: busca enquanto o usuário digita). */
export function useAtrasado<T>(valor: T, ms = 300): T {
  const [atrasado, setAtrasado] = useState(valor)
  useEffect(() => {
    const id = setTimeout(() => setAtrasado(valor), ms)
    return () => clearTimeout(id)
  }, [valor, ms])
  return atrasado
}
