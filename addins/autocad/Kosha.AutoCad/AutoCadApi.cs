// SPDX-License-Identifier: Apache-2.0
// The only place that names AutoCAD's .NET namespaces (from AcCoreMgd.dll, AcDbMgd.dll and AcMgd.dll).
// The shared DWG code (sdk/dotnet/Kosha.CadCore.Dwg) is written against the names below, and the GStarCAD
// add-in's GstarCadApi.cs maps the same names to GrxCAD.*, so one source serves both programs.
// ApplicationServices is also imported as a whole: since AutoCAD 2013, DocumentManager.Open and
// Document.CloseAndDiscard are extension methods in that namespace.
global using CadApplication = Autodesk.AutoCAD.ApplicationServices.Application;
global using CadDocumentNative = Autodesk.AutoCAD.ApplicationServices.Document;
global using Autodesk.AutoCAD.ApplicationServices;
global using Autodesk.AutoCAD.DatabaseServices;
global using Autodesk.AutoCAD.Runtime;
global using Autodesk.AutoCAD.Windows;
