import { Palette } from "@/components/palette"

export default function Home() {
  return (
    <main className="flex flex-1 items-start justify-center px-4 pt-[20vh]">
      <div className="w-full max-w-xl">
        <Palette />
      </div>
    </main>
  )
}
