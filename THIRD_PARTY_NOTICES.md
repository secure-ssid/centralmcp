# Third-party notices

centralmcp is MIT licensed. Generated API manifests and implementation patterns
also depend on upstream projects and vendor documentation with their own terms.

## Mist OpenAPI

The generated Mist operation manifest is derived from
[`mistsys/mist_openapi`](https://github.com/mistsys/mist_openapi), pinned to
version 2606.1.1 at commit
`f374cffdd5a275c7954645a306fcab7f1227e7a3`.

That repository is distributed under the MIT License. Preserve its copyright
and license notices when redistributing derived generated metadata.

## hpe-networking-mcp reference implementation

Generated-tool architecture and platform operation coverage were compared
against
[`nowireless4u/hpe-networking-mcp`](https://github.com/nowireless4u/hpe-networking-mcp),
an MIT-licensed community project. centralmcp uses its public implementation as
reference material while retaining independent clients, registration, routing,
and response handling.

## Vendor API specifications

Aruba, HPE GreenLake, ClearPass, ArubaOS, EdgeConnect, UXI, Apstra, and Axis
API specifications and documentation may be subject to vendor portal or product
terms and are not automatically covered by centralmcp's MIT license.

centralmcp does not commit vendor raw specifications unless redistribution is
explicitly permitted. Generated manifests contain operation metadata needed by
the runtime and record source provenance/digests. Users remain responsible for
accessing vendor APIs under the applicable product and documentation terms.
