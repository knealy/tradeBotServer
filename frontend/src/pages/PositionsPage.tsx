import PositionsOverview from '../components/PositionsOverview'

export default function PositionsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Positions</h1>
        <p className="text-slate-400 mt-2">Monitor and manage your open positions</p>
      </div>
      
      <PositionsOverview />
    </div>
  )
}

