import { useEffect, useRef } from 'react'

export default function Modal({ children, labelledBy, onCancel }) {
  const dialogRef = useRef(null)

  useEffect(() => {
    const dialog = dialogRef.current
    dialog.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = previousOverflow
    }
  }, [])

  return (
    <dialog ref={dialogRef} className="schedko-modal" aria-labelledby={labelledBy} onCancel={(event) => {
      event.preventDefault()
      onCancel()
    }}>
      {children}
    </dialog>
  )
}
