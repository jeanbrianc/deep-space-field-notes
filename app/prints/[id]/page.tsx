import Link from 'next/link';
import { notFound } from 'next/navigation';
import { printProductForId } from '../../print-catalog';

export default async function PrintPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = printProductForId(id);
  if (!product) notFound();
  return <main className="print-page">
    <Link className="print-back" href="/">← Back to the observatory</Link>
    <div className="print-detail">
      <figure className="poster-preview"><img src={product.previewUrl} alt={`${product.title} poster with black field-note text panels`} /></figure>
      <section className="print-copy">
        <p className="eyebrow">Deep Space Field Notes · {product.object}</p>
        <h1>{product.title}</h1>
        <p className="print-size">{product.sizeLabel}{product.checkoutUrl && ` · ${product.priceLabel}`}</p>
        <p>Captured with a Seestar smart telescope under Northern Michigan skies. An edge-to-edge portrait with black field-note panels and photography by Brian Jean.</p>
        <p className="print-disclosure">The poster crop differs from the full gallery view. Review the composition shown here.</p>
        {product.checkoutUrl ? <>
          <a className="print-link" href={product.checkoutUrl} target="_blank" rel="noopener noreferrer"><strong>Buy this print</strong></a>
          <p className="print-disclosure">Shipping and tax are calculated at checkout. Fourthwall handles secure payment, shipping, and fulfillment.</p>
        </> : <div className="print-coming-soon"><strong>Print coming soon</strong><p>This is a design preview. Sales will open after the print is reviewed and ready.</p></div>}
      </section>
    </div>
  </main>;
}
